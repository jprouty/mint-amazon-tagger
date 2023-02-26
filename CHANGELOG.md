## 1.62

- Bug fix: ["AMAZON.COM" transactions were not being considered, despite never being tagged](https://github.com/jprouty/mint-amazon-tagger/issues/133).
- Bug Fix: [Add better GUI reporting when missing email/password or "I will login myself" is checked](https://github.com/jprouty/mint-amazon-tagger/issues/144).

## 1.61

- Change log established
- Bug Fix: Reworked Amazon login flow, especially for [multi-user](https://github.com/jprouty/mint-amazon-tagger/issues/132)
- Bug Fix: Add workaround for ["RecursionError: maximum recursion depth exceeded in comparison"](https://github.com/jprouty/mint-amazon-tagger/issues/122)
- Show help tooltips in the GUI when hovering over both inputs and the labels.
- Close the chromedriver instance when dismissing the dialog, allowing for more graceful reattempts.
