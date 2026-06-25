// SPDX-License-Identifier: MIT
// Copyright (c) 2024 Skillberry Contributors

//! Context extractor filter implementation.

use async_trait::async_trait;
use regex::Regex;

use super::config::{ContextExtractorConfig, HeaderExtractionRule};
use praxis_filter::{
    FilterAction, FilterError,
    BodyAccess, BodyMode,
    parse_filter_config,
    HttpFilter, HttpFilterContext,
};

/// A header extraction rule with a pre-compiled regex.
struct CompiledRule {
    rule: HeaderExtractionRule,
    pattern: Option<Regex>,
}

/// Extracts HTTP request headers into `ctx.filter_metadata` for use by
/// downstream filters (`skill_resolver`, `vmcp_manager`).
///
/// ```yaml
/// filter: context_extractor
/// headers:
///   - name: x-skillberry-env-id
///     metadata_key: env_id
///     default: "default-env"
///     required: true
///     pattern: "^[a-zA-Z0-9_-]+$"
///     max_length: 64
/// ```
pub struct ContextExtractorFilter {
    rules: Vec<CompiledRule>,
    /// Global max-length constraint applied to every extracted value.
    global_max_length: Option<usize>,
    /// Global regex compiled once at construction time; applied to every extracted value.
    global_pattern: Option<Regex>,
}

impl ContextExtractorFilter {
    /// Create from YAML config. Compiles all regex patterns at construction time.
    pub fn from_config(config: &serde_yaml::Value) -> Result<Box<dyn HttpFilter>, FilterError> {
        let cfg: ContextExtractorConfig = parse_filter_config("context_extractor", config)?;

        if cfg.headers.is_empty() {
            return Err("context_extractor: 'headers' must not be empty".into());
        }

        let mut rules = Vec::with_capacity(cfg.headers.len());

        for rule_cfg in cfg.headers {
            // Validate that the header name is a legal HTTP header name.
            http::HeaderName::from_bytes(rule_cfg.name.as_bytes()).map_err(|e| -> FilterError {
                format!("context_extractor: invalid header name '{}': {e}", rule_cfg.name).into()
            })?;

            let pattern = rule_cfg
                .pattern
                .as_deref()
                .map(|p| {
                    Regex::new(p).map_err(|e| -> FilterError {
                        format!(
                            "context_extractor: invalid regex pattern '{}' for header '{}': {e}",
                            p, rule_cfg.name
                        )
                        .into()
                    })
                })
                .transpose()?;

            rules.push(CompiledRule { rule: rule_cfg, pattern });
        }

        let (global_max_length, global_pattern) = if let Some(ref val) = cfg.validation {
            let pattern = val
                .pattern
                .as_deref()
                .map(|p| {
                    Regex::new(p).map_err(|e| -> FilterError {
                        format!(
                            "context_extractor: invalid global validation pattern '{}': {e}",
                            p
                        )
                        .into()
                    })
                })
                .transpose()?;
            (val.max_length, pattern)
        } else {
            (None, None)
        };

        Ok(Box::new(Self { rules, global_max_length, global_pattern }))
    }
}

#[async_trait]
impl HttpFilter for ContextExtractorFilter {
    fn name(&self) -> &'static str {
        "context_extractor"
    }

    fn request_body_access(&self) -> BodyAccess {
        BodyAccess::None
    }

    fn request_body_mode(&self) -> BodyMode {
        BodyMode::Stream
    }

    async fn on_request(&self, ctx: &mut HttpFilterContext<'_>) -> Result<FilterAction, FilterError> {
        for compiled in &self.rules {
            let rule = &compiled.rule;

            let raw = ctx.request.headers
                .get(&rule.name)
                .and_then(|v| v.to_str().ok())
                .map(str::to_string);

            let value = match raw {
                Some(v) => v,
                None => {
                    if let Some(ref default) = rule.default {
                        tracing::debug!(
                            header = %rule.name,
                            metadata_key = %rule.metadata_key,
                            "header absent, using default"
                        );
                        default.clone()
                    } else if rule.required {
                        tracing::warn!(header = %rule.name, "required header missing");
                        return Err(format!(
                            "context_extractor: required header '{}' is missing",
                            rule.name
                        )
                        .into());
                    } else {
                        tracing::debug!(header = %rule.name, "optional header absent, skipping");
                        continue;
                    }
                }
            };

            validate_value(&value, rule, &compiled.pattern, self.global_max_length, self.global_pattern.as_ref())?;

            tracing::debug!(
                header = %rule.name,
                metadata_key = %rule.metadata_key,
                "extracted header to metadata"
            );
            ctx.filter_metadata.insert(rule.metadata_key.clone(), value);
        }

        Ok(FilterAction::Continue)
    }
}

/// Validate an extracted value against per-rule and global constraints.
fn validate_value(
    value: &str,
    rule: &HeaderExtractionRule,
    compiled_pattern: &Option<Regex>,
    global_max_length: Option<usize>,
    global_pattern: Option<&Regex>,
) -> Result<(), FilterError> {
    if let Some(max_len) = rule.max_length {
        if value.len() > max_len {
            return Err(format!(
                "context_extractor: header '{}' value exceeds max_length {} (got {})",
                rule.name, max_len, value.len()
            )
            .into());
        }
    }

    if let Some(pattern) = compiled_pattern {
        if !pattern.is_match(value) {
            return Err(format!(
                "context_extractor: header '{}' value does not match required pattern",
                rule.name
            )
            .into());
        }
    }

    if let Some(max_len) = global_max_length {
        if value.len() > max_len {
            return Err(format!(
                "context_extractor: header '{}' value exceeds global max_length {} (got {})",
                rule.name, max_len, value.len()
            )
            .into());
        }
    }

    if let Some(pattern) = global_pattern {
        if !pattern.is_match(value) {
            return Err(format!(
                "context_extractor: header '{}' value does not match global validation pattern",
                rule.name
            )
            .into());
        }
    }

    Ok(())
}
