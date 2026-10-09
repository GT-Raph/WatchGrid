# WatchGrid interface

The existing navy navigation, blue actions, and time-aligned status grid carry the identity of the technical specification. The Django conversion retains them.

Palette: navigation #13203b, page #eef1f6, surface #ffffff, text #142038, action #2a6fdb, muted text #4b566b. Status colors are reserved for health and paired with labels or cell shapes.

Typography: locally hosted Instrument Sans for UI and JetBrains Mono for URLs and measured values. Tabular numbers keep columns stable.

Layout: left-aligned navigation and content; a compact filter bar, six health counters, and a full-width monitor/time grid. Detail pages put evidence before configuration. Mobile navigation expands in place and tables scroll within their own region.

    Navigation | Organization / Project / Period
               | Health counters
               | Monitor x time grid
               | Recent incidents

Review against the brief: the UX search suggested a dark operations landing page. The product is an authenticated work surface, so its hero, promotional CTA, glow, and red primary actions are rejected. Preserve the existing light/dark dashboard and blue action hierarchy.

No page entrance animations. Pointer press feedback lasts 120ms; keyboard actions and reduced-motion use immediate feedback. Forms use native controls, visible labels, field errors, and an error summary. All core flows work with JavaScript disabled.
