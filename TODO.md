# Cognitrix Implementation TODO

## Configuration

| Component | Choice |
|-----------|--------|
| **Vector Store** | ChromaDB (local, embedded) |
| **Embedding Model** | all-MiniLM-L6-v2 (fast, 22MB) |
| **Safety Mode** | Risk-based with learning |
| **Planning LLM** | Same as agent |
| **Memory Storage** | Separate ChromaDB instance |

## Phase 1: Foundation (Week 1)

### Phase 1: Workflow Execution [COMPLETED]
- [x] Create `cognitrix/teams/workflow_executor.py`
- [x] Implement `WorkflowExecutor` class with step execution
- [x] Add parallel execution for independent steps
- [x] Replace stub in `TeamManager.leader_coordinate_workflow()`
- [x] Test end-to-end workflow execution

**Files:**
- New: `cognitrix/teams/workflow_executor.py`
- Modify: `cognitrix/teams/base.py`

---

### Phase 4: Retry Logic [COMPLETED]
- [x] Create `cognitrix/utils/retry.py`
- [x] Implement exponential backoff with jitter
- [x] Add retry decorator
- [x] Integrate into `AgentManager.call_tools()`
- [x] Add param recovery using LLM on failure

**Files:**
- New: `cognitrix/utils/retry.py`
- New: `cognitrix/tools/resilient_tool_wrapper.py`
- Modify: `cognitrix/agents/base.py`

---

## Phase 2: Intelligence (Week 2)

### Phase 5: Structured Planning [COMPLETED]
- [x] Create `cognitrix/prompts/planning.py` with JSON planning prompt
- [x] Create Pydantic models for TaskPlan, Step
- [x] Create `cognitrix/planning/structured_planner.py`
- [x] Implement dependency resolution
- [x] Replace text-based workflow creation
- [x] Add plan validation

**Files:**
- New: `cognitrix/planning/__init__.py`
- New: `cognitrix/planning/structured_planner.py`
- New: `cognitrix/prompts/planning.py`
- Modify: `cognitrix/teams/base.py`

---

### Phase 2: Memory System [COMPLETED]
- [x] Create `cognitrix/memory/base.py` with abstract interface
- [x] Create `cognitrix/memory/chroma_store.py` with ChromaDB
- [x] Create `cognitrix/memory/hybrid_context.py`
- [x] Implement importance scoring
- [x] Create `cognitrix/agents/context_manager.py` for hybrid context
- [x] Integrate into session management
- [x] Add memory persistence after each exchange

**Files:**
- New: `cognitrix/memory/__init__.py`
- New: `cognitrix/memory/base.py`
- New: `cognitrix/memory/chroma_store.py`
- New: `cognitrix/memory/hybrid_context.py`
- Modify: `cognitrix/models/agent.py`
- Modify: `cognitrix/sessions/base.py`

---

## Phase 3: Routing & Safety (Week 3)

### Phase 3: Agent Router [COMPLETED]
- [x] Create `cognitrix/agents/capability_registry.py`
- [x] Implement agent capability extraction with embeddings
- [x] Create `cognitrix/agents/router.py`
- [x] Implement task-agent similarity matching
- [x] Add task decomposition for complex queries
- [x] Integrate router into team workflow

**Files:**
- New: `cognitrix/agents/capability_registry.py`
- New: `cognitrix/agents/router.py`
- Modify: `cognitrix/teams/base.py`

---

### Phase 6: Safety Gates [COMPLETED]
- [x] Create `cognitrix/safety/destructive_ops.py` with risk categories
- [x] Implement `DestructiveOpDetector`
- [x] Create `cognitrix/safety/approval_gate.py`
- [x] Implement CLI approval handler
- [x] Implement WebSocket approval handler
- [x] Add approval caching (session & permanent)
- [x] Integrate into `AgentManager.call_tools()`
- [x] Add risk metadata to destructive tools

**Files:**
- New: `cognitrix/safety/__init__.py`
- New: `cognitrix/safety/approval_gate.py`
- New: `cognitrix/safety/destructive_ops.py`
- Modify: `cognitrix/agents/base.py`
- Modify: `cognitrix/tools/misc.py`

---

## Critical Bug Fixes [COMPLETED]

### Fix 1: Workflow Executor Dependency Lookup
- [x] Fixed dependency resolution using completed_results dict
- [x] Updated _build_step_prompt to use correct data structure

### Fix 2: Sync Embedding Blocking Event Loop
- [x] Added async embedding wrapper using run_in_executor
- [x] Updated store() and retrieve() to use async embeddings

### Fix 3: Permanent Cache Persistence
- [x] Added cache persistence to ~/.cognitrix/approval_cache.json
- [x] Added _load_permanent_cache() and _save_permanent_cache()

### Fix 4: Hash Length
- [x] Changed from 16-char truncated to full 64-char SHA256 hash

### Fix 5: Input Sanitization
- [x] Added _sanitize_for_prompt() with HTML escaping
- [x] Applied sanitization to tool name, description, params, error

### Fix 6: Shared Embedding Model
- [x] Created singleton wrapper in cognitrix/utils/embedding_model.py
- [x] Updated chroma_store.py and capability_registry.py to use shared model

**Files:**
- New: `cognitrix/utils/embedding_model.py`
- Modify: `cognitrix/teams/workflow_executor.py`
- Modify: `cognitrix/memory/chroma_store.py`
- Modify: `cognitrix/safety/approval_gate.py`
- Modify: `cognitrix/tools/resilient_tool_wrapper.py`
- Modify: `cognitrix/agents/capability_registry.py`

---

## Dependencies [COMPLETED]

Updated `pyproject.toml`:
```toml
chromadb = "^1.5.0"           # Was ^0.6.0
sentence-transformers = "^5.2.0"  # Was ^3.0.0
numpy = "^2.0.0"              # Was ^1.26.0
```

---

## Testing [COMPLETED]

- [x] Unit tests for each new component
- [x] Integration tests for workflow execution
- [x] Memory retrieval accuracy tests
- [x] Safety gate trigger tests
- [x] End-to-end task benchmarks

**Test Files:**
- `tests/test_workflow_executor.py` - 32 tests
- `tests/test_memory.py` - 36 tests
- `tests/test_retry.py` - 28 tests
- `tests/test_safety.py` - 40 tests
- `tests/test_planning.py` - 34 tests
- `tests/test_router.py` - 38 tests

**Total: ~200 tests**

Run with: `pytest tests/`

---

## Progress Summary

| Phase | Status | Completion |
|-------|--------|------------|
| Phase 1: Workflow Execution | Completed | 100% |
| Phase 4: Retry Logic | Completed | 100% |
| Phase 5: Structured Planning | Completed | 100% |
| Phase 2: Memory System | Completed | 100% |
| Phase 3: Agent Router | Completed | 100% |
| Phase 6: Safety Gates | Completed | 100% |
| Dependencies | Completed | 100% |
| Critical Bug Fixes | Completed | 100% |
| Testing | Completed | 100% |

**Overall Progress:** 100% ✅

**Status:** All implementation tasks completed and ready for production use!
