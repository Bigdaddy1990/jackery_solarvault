"""Authentication-adjacent caches for the Jackery cloud client.

The login endpoint and crypto now live in the ``client.api`` monolith; this
package retains the discovery and daily-data caches imported by the
coordinator via their full module paths.
"""
