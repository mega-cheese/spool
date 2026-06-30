# Review Queue

## 1. Conflicting sources for decision

Severity: high
Reason: conflicting_sources
Candidate decision: feature flags vendor

Summary:
Slack approval selects LaunchDarkly, but Google Doc Decision section selects ConfigCat. The sources have different timestamps, but no automatic authority rule is configured.

Recommended action:
Human review required: choose which source is authoritative before writing this decision to clean memory.

Options:

* LaunchDarkly - Slack: Approved: choose LaunchDarkly for feature flags now.
* ConfigCat - Google Doc Decision section: Final decision: use ConfigCat for feature flags.

Sources:
* Hannah Lee: Approved: choose LaunchDarkly for feature flags now.
* Maya Chen: Final decision: use ConfigCat for feature flags.
