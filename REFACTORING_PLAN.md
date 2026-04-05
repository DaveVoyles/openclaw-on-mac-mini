# Phase 2 - Code Refactoring Plan

## Order of Implementation

### 1. Foundation (Custom Exceptions & Structured Logging)
- [ ] Create src/exceptions.py with custom exception hierarchy
- [ ] Create src/logging_config.py with structlog configuration  
- [ ] Update imports in existing modules
- [ ] Test: pytest tests/ -v

### 2. Utility Modules
- [ ] Create src/utils/text.py (truncate, split functions)
- [ ] Create src/utils/time.py (duration parsing/formatting)
- [ ] Create src/utils/discord.py (embed helpers)
- [ ] Migrate existing utility functions
- [ ] Test: pytest tests/test_utils.py -v

### 3. Decorator Patterns
- [ ] Create src/decorators.py (retry, timing decorators)
- [ ] Apply decorators to API calls
- [ ] Test: pytest tests/ -k "retry or timeout" -v

### 4. God Class Refactoring: ConversationStore
- [ ] Create src/memory/persistence.py (ConversationPersistence)
- [ ] Create src/memory/repository.py (ConversationRepository)
- [ ] Create src/memory/formatter.py (ConversationFormatter)
- [ ] Update src/memory.py to use new classes
- [ ] Test: pytest tests/test_memory.py -v

### 5. God Class Refactoring: ApprovalStore
- [ ] Create src/approvals/persistence.py (ApprovalPersistence)
- [ ] Create src/approvals/repository.py (ApprovalRepository)
- [ ] Create src/approvals/notifier.py (ApprovalNotifier)
- [ ] Update src/approvals.py to use new classes
- [ ] Test: pytest tests/test_approvals*.py -v

### 6. Dataclasses for Complex Parameters
- [ ] Create dataclasses for chat_stream config
- [ ] Create dataclasses for other complex function parameters
- [ ] Test: pytest tests/test_llm*.py -v

### 7. Builder Patterns
- [ ] Create src/builders/embed_builder.py
- [ ] Update Discord embed creation to use builder
- [ ] Test: pytest tests/ -k "embed" -v

### 8. Final Integration & Cleanup
- [ ] Run full test suite
- [ ] Update documentation
- [ ] Check for regressions

## Success Criteria
- All tests passing
- No new warnings or errors
- Code more maintainable
- Better type hints
- Improved error handling
