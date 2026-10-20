"""Evidence retrieval and document-assistant application service."""
import os

# Avoid framework telemetry and writes outside the configured application data.
os.environ.setdefault('HAYSTACK_TELEMETRY_ENABLED', 'false')
