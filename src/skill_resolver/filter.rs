// SPDX-License-Identifier: MIT
// Copyright (c) 2024 Skillberry Contributors

//! Skill resolver filter implementation.

use std::time::Duration;

use async_trait::async_trait;
use reqwest::Client;
use serde::Deserialize;

use super::config::SkillResolverConfig;
use praxis_filter::{
    FilterAction, FilterError,
    BodyAccess, BodyMode,
    parse_filter_config,
    HttpFilter, HttpFilterContext,
};

/// Response from skillberry-store GET /skills/{uuid_or_name} endpoint.
#[derive(Debug, Deserialize)]
struct SkillResponse {
    uuid: String,
    #[allow(dead_code)]
    name: String,
    #[allow(dead_code)]
    description: Option<String>,
}

/// Resolves skill UUIDs from environment variables.
///
/// Reads `SKILL_UUID` or `SKILL_NAME` once at construction time and stores
/// the resolved value. If `SKILL_NAME` is provided, an HTTP request to
/// skillberry-store is made per-request to look up the UUID.
///
/// The resolved UUID is stored in `filter_metadata["skill_uuid"]` for use
/// by downstream filters (e.g., `vmcp_manager`).
pub struct SkillResolverFilter {
    http_client: Client,
    store_base_url: String,
    /// Value of `SKILL_UUID` env var read once at startup, if set.
    skill_uuid: Option<String>,
    /// Value of `SKILL_NAME` env var read once at startup, if set.
    skill_name: Option<String>,
    #[allow(dead_code)]
    timeout: Duration,
}

impl SkillResolverFilter {
    /// Create from YAML config. Reads env vars once at construction time.
    pub fn from_config(config: &serde_yaml::Value) -> Result<Box<dyn HttpFilter>, FilterError> {
        let cfg: SkillResolverConfig = parse_filter_config("skill_resolver", config)?;

        if cfg.store_base_url.is_empty() {
            return Err("skill_resolver: 'store_base_url' must not be empty".into());
        }

        let http_client = Client::builder()
            .timeout(Duration::from_millis(cfg.timeout_ms))
            .build()
            .map_err(|e| -> FilterError {
                format!("skill_resolver: failed to create HTTP client: {e}").into()
            })?;

        let skill_uuid = std::env::var(&cfg.skill_uuid_env).ok();
        let skill_name = std::env::var(&cfg.skill_name_env).ok();

        Ok(Box::new(Self {
            http_client,
            store_base_url: cfg.store_base_url,
            skill_uuid,
            skill_name,
            timeout: Duration::from_millis(cfg.timeout_ms),
        }))
    }

    async fn lookup_skill_by_name(&self, skill_name: &str) -> Result<SkillResponse, FilterError> {
        let url = format!("{}/skills/{}", self.store_base_url, skill_name);

        tracing::debug!(
            skill_name = %skill_name,
            url = %url,
            "looking up skill via API"
        );

        let response = self.http_client
            .get(&url)
            .send()
            .await
            .map_err(|e| -> FilterError {
                if e.is_timeout() {
                    tracing::error!(skill_name = %skill_name, "skill lookup timed out");
                    FilterError::from("skill lookup timed out")
                } else if e.is_connect() {
                    tracing::error!(
                        skill_name = %skill_name,
                        error = %e,
                        "failed to connect to skillberry-store"
                    );
                    Box::new(std::io::Error::new(
                        std::io::ErrorKind::Other,
                        "skillberry-store is unreachable",
                    ))
                } else {
                    tracing::error!(
                        skill_name = %skill_name,
                        error = %e,
                        "skill lookup request failed"
                    );
                    FilterError::from(format!("skill lookup failed: {e}"))
                }
            })?;

        let status = response.status();

        if status.is_success() {
            response.json::<SkillResponse>().await
                .map_err(|e| -> FilterError {
                    tracing::error!(
                        skill_name = %skill_name,
                        error = %e,
                        "failed to parse skill response"
                    );
                    FilterError::from(format!("invalid skill response: {e}"))
                })
        } else if status.as_u16() == 404 {
            tracing::warn!(skill_name = %skill_name, "skill not found in store");
            Err(FilterError::from(format!("skill '{}' not found", skill_name)))
        } else {
            tracing::error!(
                skill_name = %skill_name,
                status = %status,
                "skill lookup returned error status"
            );
            Err(FilterError::from(format!("skill lookup failed with status {}", status)))
        }
    }
}

#[async_trait]
impl HttpFilter for SkillResolverFilter {
    fn name(&self) -> &'static str {
        "skill_resolver"
    }

    fn request_body_access(&self) -> BodyAccess {
        BodyAccess::None
    }

    fn request_body_mode(&self) -> BodyMode {
        BodyMode::Stream
    }

    async fn on_request(&self, ctx: &mut HttpFilterContext<'_>) -> Result<FilterAction, FilterError> {
        // Priority 1: direct UUID from env (read at startup)
        if let Some(ref skill_uuid) = self.skill_uuid {
            tracing::info!(skill_uuid = %skill_uuid, "using skill UUID from environment variable");
            ctx.filter_metadata.insert("skill_uuid".to_string(), skill_uuid.clone());
            ctx.filter_metadata.insert("skill_resolution_method".to_string(), "env_uuid".to_string());
            return Ok(FilterAction::Continue);
        }

        // Priority 2: skill name from env (read at startup), resolve UUID via API per-request
        if let Some(ref skill_name) = self.skill_name {
            tracing::info!(skill_name = %skill_name, "resolving skill UUID from name via API");

            match self.lookup_skill_by_name(skill_name).await {
                Ok(skill) => {
                    tracing::info!(
                        skill_name = %skill_name,
                        skill_uuid = %skill.uuid,
                        "successfully resolved skill UUID"
                    );
                    ctx.filter_metadata.insert("skill_uuid".to_string(), skill.uuid);
                    ctx.filter_metadata.insert("skill_name".to_string(), skill_name.clone());
                    ctx.filter_metadata.insert("skill_resolution_method".to_string(), "api_lookup".to_string());
                    return Ok(FilterAction::Continue);
                }
                Err(e) => {
                    tracing::warn!(
                        skill_name = %skill_name,
                        error = %e,
                        "failed to resolve skill, continuing without skill"
                    );
                    ctx.filter_metadata.insert("skill_resolution_error".to_string(), e.to_string());
                    return Ok(FilterAction::Continue);
                }
            }
        }

        // Priority 3: neither UUID nor name configured
        tracing::debug!("no skill UUID or name configured, continuing without skill");
        ctx.filter_metadata.insert("skill_resolution_method".to_string(), "none".to_string());
        Ok(FilterAction::Continue)
    }
}
