"""The local AIBuildAI Workspace in the browser.

Three process roles have three lifetimes. Caddy owns the fixed loopback port.
The backend routes each Run to one isolated projection worker. The live member
owns only identity and Pause. The browser owns its timeline state. The backend
offers Pause and Resume, with no generic command route or second Run state.
"""
