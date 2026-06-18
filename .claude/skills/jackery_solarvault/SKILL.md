```markdown
# jackery_solarvault Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill introduces the core development patterns and conventions used in the `jackery_solarvault` Python repository. It covers file organization, import/export styles, commit message habits, and testing patterns. While no specific frameworks are detected, the repository follows clear Pythonic conventions and supports maintainable, readable code.

## Coding Conventions

### File Naming
- Use **snake_case** for all file names.
  - **Example:**
    ```plaintext
    solar_controller.py
    battery_manager.py
    ```

### Import Style
- Use **relative imports** within the codebase.
  - **Example:**
    ```python
    from .battery_manager import BatteryManager
    ```

### Export Style
- Use **named exports** by explicitly defining what is available for import.
  - **Example:**
    ```python
    __all__ = ["BatteryManager", "SolarController"]
    ```

### Commit Messages
- Freeform, concise messages (average 43 characters).
- No strict prefixing required.
  - **Example:**
    ```
    Add battery status monitor
    Fix voltage calculation bug
    ```

## Workflows

### Code Contribution
**Trigger:** When adding new features or fixing bugs
**Command:** `/contribute`

1. Create a new branch for your feature or fix.
2. Write code following the coding conventions above.
3. Use relative imports and named exports.
4. Write concise commit messages.
5. Open a pull request for review.

### Testing Code
**Trigger:** When verifying code correctness
**Command:** `/test`

1. Identify or create test files matching the `*.test.ts` pattern.
2. Write tests for new or changed functionality.
3. Run the tests using the appropriate test runner (framework unknown; consult project documentation or maintainers).
4. Ensure all tests pass before merging.

## Testing Patterns

- Test files use the `*.test.ts` naming pattern.
- Testing framework is **unknown**; check for scripts or documentation for running tests.
- Place tests alongside or in a dedicated test directory.

**Example test file name:**
```plaintext
battery_manager.test.ts
```

## Commands
| Command      | Purpose                                 |
|--------------|-----------------------------------------|
| /contribute  | Start a new code contribution workflow  |
| /test        | Run or write tests for the codebase     |
```
