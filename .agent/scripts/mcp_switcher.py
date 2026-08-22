import json
import sys
import os

CONFIG_PATH = os.environ.get("ANTIGRAVITY_MCP_CONFIG", os.path.expanduser("~/.gemini/antigravity/mcp_config.json"))

def switch(enable_name, disable_name):
    if not os.path.exists(CONFIG_PATH):
        print(f"Error: Config not found at {CONFIG_PATH}")
        return

    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
    
    servers = config.get("mcpServers", {})
    
    # Normalizing keys: they might be 'atlassian' or '_atlassian'
    all_keys = list(servers.keys())
    
    actual_enable_key = next((k for k in all_keys if k == enable_name or k == f"_{enable_name}"), None)
    actual_disable_key = next((k for k in all_keys if k == disable_name or k == f"_{disable_name}"), None)

    changed = False

    # 1. Enable the target
    if actual_enable_key:
        if actual_enable_key.startswith("_"):
            # Move from _name to name
            servers[enable_name] = servers.pop(actual_enable_key)
            servers[enable_name].pop("disabled", None) # Clean up legacy flags
            print(f"Enabled {enable_name}")
            changed = True
        else:
            print(f"{enable_name} already enabled.")
    else:
        print(f"Warning: '{enable_name}' not found in mcp_config.json")

    # 2. Disable the other
    if actual_disable_key:
        if not actual_disable_key.startswith("_"):
            # Move from name to _name
            servers[f"_{disable_name}"] = servers.pop(actual_disable_key)
            print(f"Disabled {disable_name}")
            changed = True
        else:
            print(f"{disable_name} already disabled.")
    else:
        print(f"Warning: '{disable_name}' not found in mcp_config.json")

    if changed:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
        print("Config updated. MCP servers should reload automatically.")
    else:
        print("No changes made to config.")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python mcp_switcher.py <enable_server> <disable_server>")
        sys.exit(1)
    
    # Case insensitive check
    e = sys.argv[1].lower()
    d = sys.argv[2].lower()
    switch(e, d)
