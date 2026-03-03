# install-hub-agent

Download and install an Agent from the OpenAkita Platform Agent Store to local.

## Tools

- `install_hub_agent` - Download and install an Agent from the hub
- `get_hub_agent_detail` - View detailed info about an Agent before installing

## Usage

Use this skill when the user wants to:
- Install an Agent from the hub marketplace
- Download a shared Agent to use locally
- View details of an Agent before installing

## Parameters

### install_hub_agent
- `agent_id` (required): The platform Agent ID to install
- `force` (optional): Force overwrite if local ID conflict

### get_hub_agent_detail
- `agent_id` (required): The platform Agent ID to inspect

## Examples

- "安装 customer-service-pro Agent"
- "看看这个 Agent 的详情"
- "从 Hub 下载 project-manager Agent"
