# Provider Wizard 13.2.1 Hotfix

This hotfix targets **LLM Budget Gateway 13.2.0**.

It fixes the provider wizard's first page by changing it from an implicit card-to-next-step action to an explicit, accessible selection followed by a **Continue** button.

It also adds an explicit **Custom provider** option with configurable:

- connection name and unique slug;
- optional API key or token;
- base URL;
- model-list path;
- authentication header;
- authentication prefix;
- extra headers JSON;
- models array field;
- model ID field.

The same provider type, including Custom provider, can be added any number of times under distinct unique slugs.

## Apply

Extract this hotfix anywhere, then run:

```bash
python apply_hotfix.py /path/to/llm-budget-gateway-13.2.0
```

The script creates a backup under:

```text
.gateway-console/hotfix-backup-13.2.1/
```

It then rebuilds the production frontend. Restart `gateway-system` and hard-refresh the browser.
