# LTP Tuner V2 — Instance Configuration Guide

## Overview

`ltp-tuner-v2` is a fine-tuned model for framework-native tuning generation, approved by Chords with a 94/94 eval score. This guide covers the instance-config flip required to enable it.

## What Changed

1. **Model Registry** (`config/models.yaml`):
   - Added `ltp-tuner-v2` entry under Specialty models
   - Local Ollama provider, `ltp-tuner-v2:latest` tag

2. **Local Tier Routing** (`src/models/local_fallback.py`):
   - Added `resolve_tuner_model()` function
   - LOCAL tier now prefers tuner for `operation_type="tuning"` work

3. **Provider Chain** (`src/models/provider.py`):
   - Tier 3 fallback checks for tuner when falling back on tuning work

## Instance Setup Steps

### 1. Pull the Model

```bash
# On the host machine running Ollama
ollama pull ltp-tuner-v2:latest
```

### 2. Verify Installation

```bash
# Check the model is available
curl http://localhost:11434/api/tags | grep ltp-tuner-v2
```

### 3. Restart Cove (if running)

```bash
cd /path/to/cove
docker compose restart app
```

### 4. Verify in Mission Control

- Navigate to Settings → Models
- Confirm `ltp-tuner-v2` appears in the registry
- Team agents can now request it via the tuning slot

## Team Agent Usage

Team agents can request the tuner as a model candidate via the tuning slot:

```yaml
# In agent.yaml or via Team page model manager
agent_id: "your-agent"
slots:
  tuning:
    primary: "ltp-tuner-v2"
    fallback: "qwen3-30b-moe"  # or other local model
```

When `operation_type="tuning"` is passed to `invoke_with_fallback()`, the LOCAL tier will automatically prefer `ltp-tuner-v2` if installed.

## Hard Boundaries (Chords Directive)

- ✅ **LOCAL tier routing**: Tuner available for tuning-shaped work
- ✅ **Team candidate**: Agents can explicitly request tuner
- ❌ **NOT the LT/public Drop path**: Tuner is local-only
- ❌ **NEVER merge gate**: No automatic merges based on tuner output
- ❌ **NEVER unsupervised posting**: Human approval required for all posts

## Monitoring

### Refusal Rate Tracking

Watch for the 5/94 team-role refusal rate in the first live week:

```bash
# Check recent tuning attempts
cd /path/to/cove
docker compose logs app --since 24h | grep -iE "tuner|tuning.*refused|team.*role"
```

Log observations in `jw_metrics` table:
- `operation_type="tuning"`
- `model_used="ltp-tuner-v2:latest"`
- `succeeded` field tracks success/failure

### Expected Behavior

- If tuner is installed: Tuning work routes to `ltp-tuner-v2`
- If tuner is NOT installed: Falls back to best available local model
- If tuner fails: Falls back through cloud → local chain per normal fallback

## Troubleshooting

### "Tuner not found" in logs

```bash
# Verify Ollama can see the model
curl http://host.docker.internal:11434/api/tags | jq '.models[].name'

# If missing, pull it:
ollama pull ltp-tuner-v2:latest
```

### Tuner not appearing in registry

- Ensure `config/models.yaml` has the entry
- Restart the Cove container to pick up registry changes
- Check for YAML syntax errors: `python3 -c "import yaml; yaml.safe_load(open('config/models.yaml'))"`

## Rollback

To disable tuner routing:

1. Remove or comment out the `ltp-tuner-v2` entry in `config/models.yaml`
2. Restart Cove
3. Local tier will fall back to `recommend_local()` picks

---

**Ticket**: #D44  
**Approved**: Chords (2026-07-12)  
**Eval Score**: 94/94 (Canon verbatim)
