# Task Plan: example-task

> This is the authoritative plan file. It is the single source of truth for this task.
> <!-- 中文注释：本文件是唯一权威计划文件。 -->

## Task Summary

Add a user preferences endpoint to the API with GET and PUT methods.

## Goals

- Implement `GET /api/preferences` returning current user preferences.
- Implement `PUT /api/preferences` to update preferences.
- Add input validation for all preference fields.
- Write unit and integration tests.

## Phases

### Phase 1: Design
- [ ] Define preference data model
- [ ] Define API request/response schemas
- [ ] Review with team

### Phase 2: Implementation
- [ ] Implement GET endpoint
- [ ] Implement PUT endpoint
- [ ] Add input validation
- [ ] Add error handling

### Phase 3: Testing
- [ ] Write unit tests for both endpoints
- [ ] Write integration tests
- [ ] Test edge cases (empty preferences, invalid input)
- [ ] All tests pass

### Phase 4: Review
- [ ] Code review
- [ ] Documentation update
- [ ] Final checkpoint

## Done Criteria

- GET and PUT endpoints are implemented and tested.
- Input validation rejects invalid data.
- All tests pass with ≥ 95% coverage on new code.
- Code review approved.
- `plan-doctor.py` passes with no warnings.

## Governance Level

L1 (LIGHT_CONTROLLED)

## Dependencies

- Database migration for preferences table (must be completed first).

## Risks

- Preferences schema may change during review - plan for iteration.
- Performance impact of preference lookups - add caching if needed.

## Checkpoint Schedule

- Checkpoint after Phase 1 (design approved)
- Checkpoint after Phase 2 (implementation complete)
- Checkpoint after Phase 3 (tests pass)
- Final checkpoint after Phase 4 (review approved)

## Notes

- This is an example plan file for documentation purposes.
- Replace all content with your actual task details.
