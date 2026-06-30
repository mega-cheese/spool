# Decision

Final decision: use LaunchDarkly for feature flags.

# Owner

Priya Shah owns implementation.

# Rationale

LaunchDarkly supports audit requirements and the SDK is already tested in staging.

# Rejected Alternatives

ConfigCat was cheaper, but migration would take longer because we would need to rebuild the flag evaluation wrapper.

# Tradeoff

The tradeoff with LaunchDarkly is higher monthly spend.

# Timeline

Implementation can finish in about eight business days.

# Review

Review flag hygiene after 45 days.
