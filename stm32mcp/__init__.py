# SPDX-License-Identifier: MIT
"""STM32 Debug MCP Server package.

The MCP server instance lives in stm32mcp.core. Importing the tools_* modules
registers their @mcp.tool functions onto that shared instance. The entry point
(stm32_probe_mcp.py) imports all of them and then calls core.mcp.run().
"""
