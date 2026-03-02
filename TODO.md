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

### Phase 1: Workflow Execution [IN PROGRESS]
- [ ] Create `cognitrix/teams/workflow_executor.py`
- [ ] Implement `WorkflowExecutor` class with step execution
- [ ] Add parallel execution for independent steps
- [ ] Replace stub in `TeamManager.leader_coordinate_workflow()`
- [ ] Test end-to-end workflow execution

**Files:**
- New: `cognitrix/teams/workflow_executor.py`
- Modify: `cognitrix/teams/base.py`

---

### Phase 4: Retry Logic [PENDING]
- [ ] Create `cognitrix/utils/retry.py`
- [ ] Implement exponential backoff with jitter
- [ ] Add retry decorator
- [ ] Integrate into `AgentManager.call_tools()`
- [ ] Add param recovery using LLM on failure

**Files:**
- New: `cognitrix/utils/retry.py`
- New: `cognitrix/tools/resilient_tool_wrapper.py`
- Modify: `cognitrix/agents/base.py`

---

## Phase 2: Intelligence (Week 2)

### Phase 5: Structured Planning [PENDING]
- [ ] Create `cognitrix/prompts/planning.py` with JSON planning prompt
- [ ] Create Pydantic models for TaskPlan, Step
- [ ] Create `cognitrix/planning/structured_planner.py`
- [ ] Implement dependency resolution
- [ ] Replace text-based workflow creation
- [ ] Add plan validation

**Files:**
- New: `cognitrix/planning/__init__.py`
- New: `cognitrix/planning/structured_planner.py`
- New: `cognitrix/prompts/planning.py`
- Modify: `cognitrix/teams/base.py`

---

### Phase 2: Memory System [PENDING]
- [ ] Create `cognitrix/memory/base.py` with abstract interface
- [ ] Create `cognitrix/memory/chroma_store.py` with ChromaDB
- [ ] Create `cognitrix/memory/hybrid_context.py`
- [ ] Implement importance scoring
- [ ] Create `cognitrix/agents/context_manager.py` for hybrid context
- [ ] Integrate into session management
- [ ] Add memory persistence after each exchange

**Files:**
- New: `cognitrix/memory/__init__.py`
- New: `cognitrix/memory/base.py`
- New: `cognitrix/memory/chroma_store.py`
- New: `cognitrix/memory/hybrid_context.py`
- Modify: `cognitrix/models/agent.py`
- Modify: `cognitrix/sessions/base.py`

---

## Phase 3: Routing & Safety (Week 3)

### Phase 3: Agent Router [PENDING]
- [ ] Create `cognitrix/agents/capability_registry.py`
- [ ] Implement agent capability extraction with embeddings
- [ ] Create `cognitrix/agents/router.py`
- [ ] Implement task-agent similarity matching
- [ ] Add task decomposition for complex queries
- [ ] Integrate router into team workflow

**Files:**
- New: `cognitrix/agents/capability_registry.py`
- New: `cognitrix/agents/router.py`
- Modify: `cognitrix/teams/base.py`

---

### Phase 6: Safety Gates [PENDING]
- [ ] Create `cognitrix/safety/destructive_ops.py` with risk categories
- [ ] Implement `DestructiveOpDetector`
- [ ] Create `cognitrix/safety/approval_gate.py`
- [ ] Implement CLI approval handler
- [ ] Implement WebSocket approval handler
- [ ] Add approval caching (session & permanent)
- [ ] Integrate into `AgentManager.call_tools()`
- [ ] Add risk metadata to destructive tools

**Files:**
- New: `cognitrix/safety/__init__.py`
- New: `cognitrix/safety/approval_gate.py`
- New: `cognitrix/safety/destructive_ops.py`
- Modify: `cognitrix/agents/base.py`
- Modify: `cognitrix/tools/misc.py`

---

## Dependencies [PENDING]

Add to `pyproject.toml`:
```toml
chromadb = "^0.6.0"
sentence-transformers = "^3.0.0"
numpy = "^1.26.0"
```

---

## Testing [PENDING]

- [ ] Unit tests for each new component
- [ ] Integration tests for workflow execution
- [ ] Memory retrieval accuracy tests
- [ ] Safety gate trigger tests
- [ ] End-to-end task benchmarks

---

## Progress Summary

| Phase | Status | Completion |
|-------|--------|------------|
| Phase 1: Workflow Execution | In Progress | 0% |
| Phase 4: Retry Logic | Pending | 0% |
| Phase 5: Structured Planning | Pending | 0% |
| Phase 2: Memory System | Pending | 0% |
| Phase 3: Agent Router | Pending | 0% |
| Phase 6: Safety Gates | Pending | 0% |
| Dependencies | Pending | 0% |
| Testing | Pending | 0% |

**Overall Progress:** 0%
