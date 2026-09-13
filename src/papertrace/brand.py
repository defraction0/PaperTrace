"""The PaperTrace mark, as a terminal can draw it.

A dot on a line of the paper, routed like a circuit trace into a red box
around the evidence — the same red as the evidence boxes in the reports. The
vectors live in `templates/brand/`: the viewer inlines the icon and the README
links the lockup. This is the same mark in box glyphs, for `papertrace`, `run`,
`init` and the guided wizard — one module, so the CLI and the wizard cannot
drift apart.

The frame is measured in terminal cells, not characters: colour markup is
invisible to a column count, and tests/test_brand_mark.py checks every row
against the border once the markup is stripped.
"""

BANNER = r"""[bold]
  ┌──────────────────────────────────┐
  │  ━━━ [red]●─┐[/red]                         │
  │  [dim]━━[/dim]    [red]└─┐[/red]   PaperTrace          │
  │  [dim]━[/dim]  [red]┌────┴┐[/red]  claims traced back  │
  │     [red]│[/red] ━━  [red]│[/red]  to their sources    │
  │     [red]└─────┘[/red]                      │
  └──────────────────────────────────┘[/bold]
"""
