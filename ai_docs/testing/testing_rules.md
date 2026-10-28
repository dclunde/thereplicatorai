# System Testing Rules

When running manual or automated tests on this system, follow these rules:

1. **Save AI credits.**
   - Automated tests must mock the AI providers; never call a real provider from the test suite.
   - For manual UI testing, check the backend for a mock/"practice test" shortcut before spending credits.

2. **Do not test the Print button against real hardware.**
   - Avoid clicking Print during routine UI testing unless you intend to start a real print on your printer.
   - Tests must never send a real print job; mock the Bambu client.
