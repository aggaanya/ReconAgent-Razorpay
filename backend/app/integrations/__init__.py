"""Outbound third-party integrations, isolated from application domain code.

Each subpackage owns its HTTP client, provider-specific parsing, and typed
exceptions so the rest of the application never depends on a provider's
wire format.
"""
