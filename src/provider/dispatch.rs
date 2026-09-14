use super::*;

const FREE_ONLY_OPENROUTER_MODEL: &str = "openrouter/free";

fn free_only_mode_enabled() -> bool {
    std::env::var("GCODE_FREE_ONLY")
        .ok()
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(false)
}

fn free_only_provider_allowed(provider: ActiveProvider) -> bool {
    matches!(provider, ActiveProvider::OpenRouter)
}

#[derive(Clone, Copy)]
pub(super) enum CompletionMode<'a> {
    Unified {
        system: &'a str,
    },
    Split {
        system_static: &'a str,
        system_dynamic: &'a str,
    },
}

impl CompletionMode<'_> {
    pub(super) fn log_suffix(self) -> &'static str {
        match self {
            CompletionMode::Unified { .. } => "",
            CompletionMode::Split { .. } => " (split)",
        }
    }

    pub(super) fn switch_log_prefix(self) -> &'static str {
        match self {
            CompletionMode::Unified { .. } => "Auto-fallback",
            CompletionMode::Split { .. } => "Auto-fallback (split)",
        }
    }
}

impl MultiProvider {
    fn enforce_free_only_request(&self, provider: ActiveProvider) -> Result<()> {
        if !free_only_mode_enabled() {
            return Ok(());
        }

        if !free_only_provider_allowed(provider) {
            anyhow::bail!(
                "FREE_ONLY_MODE blocked provider '{}'. Only OpenRouter model '{}' is allowed.",
                Self::provider_label(provider),
                FREE_ONLY_OPENROUTER_MODEL
            );
        }

        self.reconcile_auth_if_provider_missing(ActiveProvider::OpenRouter);
        let Some(openrouter) = self.openrouter_provider() else {
            anyhow::bail!(
                "FREE_ONLY_MODE requires OpenRouter credentials. No paid provider fallback is allowed."
            );
        };

        if openrouter.model() != FREE_ONLY_OPENROUTER_MODEL {
            crate::logging::warn(&format!(
                "FREE_ONLY_MODE corrected OpenRouter model '{}' -> '{}' before request",
                openrouter.model(),
                FREE_ONLY_OPENROUTER_MODEL
            ));
            openrouter.set_model(FREE_ONLY_OPENROUTER_MODEL)?;
        }

        Ok(())
    }

    pub(super) fn estimate_request_input(
        messages: &[Message],
        tools: &[ToolDefinition],
        mode: CompletionMode<'_>,
    ) -> (usize, usize) {
        let mut chars = serde_json::to_string(messages)
            .map(|value| value.len())
            .unwrap_or(0)
            + serde_json::to_string(tools)
                .map(|value| value.len())
                .unwrap_or(0);
        match mode {
            CompletionMode::Unified { system } => {
                chars += system.len();
            }
            CompletionMode::Split {
                system_static,
                system_dynamic,
            } => {
                chars += system_static.len() + system_dynamic.len();
            }
        }
        let tokens = chars / 4;
        (chars, tokens)
    }

    pub(super) async fn complete_on_provider(
        &self,
        provider: ActiveProvider,
        messages: &[Message],
        tools: &[ToolDefinition],
        system: &str,
        resume_session_id: Option<&str>,
    ) -> Result<EventStream> {
        self.enforce_free_only_request(provider)?;
        self.reconcile_auth_if_provider_missing(provider);
        match provider {
            ActiveProvider::Claude => {
                if let Some(anthropic) = self.anthropic_provider() {
                    anthropic
                        .complete(messages, tools, system, resume_session_id)
                        .await
                } else if let Some(claude) = self.claude_provider() {
                    claude
                        .complete(messages, tools, system, resume_session_id)
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "Claude credentials not available. Run `claude` to log in."
                    ))
                }
            }
            ActiveProvider::OpenAI => {
                if let Some(openai) = self.openai_provider() {
                    openai
                        .complete(messages, tools, system, resume_session_id)
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "OpenAI credentials not available. Run `gcode login --provider openai` to log in."
                    ))
                }
            }
            ActiveProvider::Copilot => {
                let copilot = self
                    .copilot_api
                    .read()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .clone();
                if let Some(copilot) = copilot {
                    copilot
                        .complete(messages, tools, system, resume_session_id)
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "GitHub Copilot is not available. Run `gcode login --provider copilot`."
                    ))
                }
            }
            ActiveProvider::Antigravity => {
                let antigravity = self.antigravity_provider();
                if let Some(antigravity) = antigravity {
                    antigravity
                        .complete(messages, tools, system, resume_session_id)
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "Antigravity is not available. Run `gcode login --provider antigravity`."
                    ))
                }
            }
            ActiveProvider::Gemini => {
                let gemini = self
                    .gemini
                    .read()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .clone();
                if let Some(gemini) = gemini {
                    gemini
                        .complete(messages, tools, system, resume_session_id)
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "Gemini is not available. Run `gcode login --provider gemini`."
                    ))
                }
            }
            ActiveProvider::Cursor => {
                let cursor = self
                    .cursor
                    .read()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .clone();
                if let Some(cursor) = cursor {
                    cursor
                        .complete(messages, tools, system, resume_session_id)
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "Cursor is not available. Run `gcode login --provider cursor`."
                    ))
                }
            }
            ActiveProvider::OpenRouter => {
                let openrouter = self
                    .openrouter
                    .read()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .clone();
                if let Some(openrouter) = openrouter {
                    openrouter
                        .complete(messages, tools, system, resume_session_id)
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "OpenRouter credentials not available. Set OPENROUTER_API_KEY environment variable."
                    ))
                }
            }
        }
    }

    pub(super) async fn complete_split_on_provider(
        &self,
        provider: ActiveProvider,
        messages: &[Message],
        tools: &[ToolDefinition],
        system_static: &str,
        system_dynamic: &str,
        resume_session_id: Option<&str>,
    ) -> Result<EventStream> {
        self.enforce_free_only_request(provider)?;
        self.reconcile_auth_if_provider_missing(provider);
        match provider {
            ActiveProvider::Claude => {
                if let Some(anthropic) = self.anthropic_provider() {
                    anthropic
                        .complete_split(
                            messages,
                            tools,
                            system_static,
                            system_dynamic,
                            resume_session_id,
                        )
                        .await
                } else if let Some(claude) = self.claude_provider() {
                    claude
                        .complete_split(
                            messages,
                            tools,
                            system_static,
                            system_dynamic,
                            resume_session_id,
                        )
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "Claude credentials not available. Run `claude` to log in."
                    ))
                }
            }
            ActiveProvider::OpenAI => {
                if let Some(openai) = self.openai_provider() {
                    openai
                        .complete_split(
                            messages,
                            tools,
                            system_static,
                            system_dynamic,
                            resume_session_id,
                        )
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "OpenAI credentials not available. Run `gcode login --provider openai` to log in."
                    ))
                }
            }
            ActiveProvider::Copilot => {
                let copilot = self
                    .copilot_api
                    .read()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .clone();
                if let Some(copilot) = copilot {
                    copilot
                        .complete_split(
                            messages,
                            tools,
                            system_static,
                            system_dynamic,
                            resume_session_id,
                        )
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "GitHub Copilot is not available. Run `gcode login --provider copilot`."
                    ))
                }
            }
            ActiveProvider::Antigravity => {
                let antigravity = self.antigravity_provider();
                if let Some(antigravity) = antigravity {
                    antigravity
                        .complete_split(
                            messages,
                            tools,
                            system_static,
                            system_dynamic,
                            resume_session_id,
                        )
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "Antigravity is not available. Run `gcode login --provider antigravity`."
                    ))
                }
            }
            ActiveProvider::Gemini => {
                let gemini = self
                    .gemini
                    .read()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .clone();
                if let Some(gemini) = gemini {
                    gemini
                        .complete_split(
                            messages,
                            tools,
                            system_static,
                            system_dynamic,
                            resume_session_id,
                        )
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "Gemini is not available. Run `gcode login --provider gemini`."
                    ))
                }
            }
            ActiveProvider::Cursor => {
                let cursor = self
                    .cursor
                    .read()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .clone();
                if let Some(cursor) = cursor {
                    cursor
                        .complete_split(
                            messages,
                            tools,
                            system_static,
                            system_dynamic,
                            resume_session_id,
                        )
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "Cursor is not available. Run `gcode login --provider cursor`."
                    ))
                }
            }
            ActiveProvider::OpenRouter => {
                let openrouter = self
                    .openrouter
                    .read()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .clone();
                if let Some(openrouter) = openrouter {
                    openrouter
                        .complete_split(
                            messages,
                            tools,
                            system_static,
                            system_dynamic,
                            resume_session_id,
                        )
                        .await
                } else {
                    Err(anyhow::anyhow!(
                        "OpenRouter credentials not available. Set OPENROUTER_API_KEY environment variable."
                    ))
                }
            }
        }
    }
}

#[cfg(test)]
mod free_only_tests {
    use super::*;

    #[test]
    fn free_only_allows_openrouter_and_blocks_other_providers() {
        assert!(free_only_provider_allowed(ActiveProvider::OpenRouter));
        assert!(!free_only_provider_allowed(ActiveProvider::Claude));
        assert!(!free_only_provider_allowed(ActiveProvider::OpenAI));
        assert!(!free_only_provider_allowed(ActiveProvider::Copilot));
        assert!(!free_only_provider_allowed(ActiveProvider::Antigravity));
        assert!(!free_only_provider_allowed(ActiveProvider::Gemini));
        assert!(!free_only_provider_allowed(ActiveProvider::Cursor));
    }
}
