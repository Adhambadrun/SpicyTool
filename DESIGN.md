---
name: Obsidian Crimson Precision
colors:
  surface: '#131315'
  surface-dim: '#131315'
  surface-bright: '#39393b'
  surface-container-lowest: '#0e0e10'
  surface-container-low: '#1b1b1d'
  surface-container: '#1f1f21'
  surface-container-high: '#2a2a2c'
  surface-container-highest: '#353437'
  on-surface: '#e5e1e4'
  on-surface-variant: '#e6bdb9'
  inverse-surface: '#e5e1e4'
  inverse-on-surface: '#303032'
  outline: '#ad8885'
  outline-variant: '#5d3f3d'
  surface-tint: '#ffb3ae'
  primary: '#ffb3ae'
  on-primary: '#68000c'
  primary-container: '#e11d2e'
  on-primary-container: '#fff8f7'
  inverse-primary: '#c0001f'
  secondary: '#4ae176'
  on-secondary: '#003915'
  secondary-container: '#00b954'
  on-secondary-container: '#004119'
  tertiary: '#ffb95f'
  on-tertiary: '#472a00'
  tertiary-container: '#a16600'
  on-tertiary-container: '#fff9f5'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#ffdad7'
  primary-fixed-dim: '#ffb3ae'
  on-primary-fixed: '#410004'
  on-primary-fixed-variant: '#930015'
  secondary-fixed: '#6bff8f'
  secondary-fixed-dim: '#4ae176'
  on-secondary-fixed: '#002109'
  on-secondary-fixed-variant: '#005321'
  tertiary-fixed: '#ffddb8'
  tertiary-fixed-dim: '#ffb95f'
  on-tertiary-fixed: '#2a1700'
  on-tertiary-fixed-variant: '#653e00'
  background: '#131315'
  on-background: '#e5e1e4'
  surface-variant: '#353437'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 40px
    fontWeight: '700'
    lineHeight: 48px
    letterSpacing: -0.025em
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 26px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Inter
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.015em
  title-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-mono-lg:
    fontFamily: JetBrains Mono
    fontSize: 18px
    fontWeight: '700'
    lineHeight: 24px
    letterSpacing: -0.02em
  label-mono-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  label-mono-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
  label-mono-xs:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.04em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  space-2xs: 0.25rem
  space-xs: 0.5rem
  space-sm: 0.75rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2rem
  space-2xl: 3rem
  space-3xl: 4rem
  gutter-mobile: 1rem
  gutter-desktop: 1.5rem
  container-max: 1200px
---

## Brand & Style

The design system establishes a high-performance, developer-grade interface tailored for frequent fliers, mileage hackers, and travel award arbitrageurs. Rooted in utility and tactical precision, it rejects the soft, generic pastel palettes common in commercial booking engines. Instead, it pairs deep carbon blacks with razor-sharp crimson accents to communicate velocity, calculation, and uncompromising efficiency.

The design movement combines **Industrial Minimalism** with **Terminal-Inspired Utilitarianism**:
- **Tactical Dark Space**: Surfaces layer subtly from pure obsidian backgrounds to graphite cards, keeping ocular fatigue low during dense flight analysis.
- **Aggressive Accent Hierarchy**: Red serves as the commanding call-to-action and primary visual anchor, immediately distinguishing high-priority state changes and search triggers.
- **Data-First Dense Typography**: Tabular monospace numerals anchor times, flight numbers, airport codes, and point values alongside a clean grotesque interface font.
- **Zero Blue Accents**: All default web blues (links, focus rings, toggles, informational badges) are stripped entirely and replaced by calibrated reds, emerald greens, and warm ambers.

## Colors

The palette operates under an uncompromising dark mode runtime. Contrast boundaries are maintained through deliberate surface steps rather than high-contrast dividing strokes.

### Color Tokens & Usage
- **Canvas Base (`#0A0A0B`)**: The deep void canvas background. Eliminates screen glare and creates infinite visual depth for foreground layers.
- **Surface Level 1 (`#141416`)**: Base cards, search modular inputs, and inactive segmented pill backings.
- **Surface Level 2 (`#1C1C1F`)**: Elevated states, active dropdown popovers, flight leg expansion panels, and modal containers.
- **Surface Level 3 / Hover (`#26262B`)**: Interactive hover states for filter chips, table rows, and secondary buttons.
- **Border / Subtle Divider (`#28282D`)**: Hairline dividers defining itinerary connectors and flight leg milestones.
- **Primary Red (`#E11D2E`)**: Main action trigger, primary brand insignia, active flight path indicator, and focused tabs.
- **Primary Hover Red (`#C31624`)**: Deepened crimson for hover, active pressed states, and high-impact actions.
- **Primary Subtle / Glow (`rgba(225, 29, 46, 0.12)`)**: Translucent background wash behind active tabs, selected date ranges, and critical price alerts.
- **Discount & Success Emerald (`#22C55E`)**: Used exclusively for value savings (`$X,XXX Off Retail`), seat availability confirmations, and completed timeline steps.
- **Warning & Cabin Amber (`#F59E0B`)**: Designates mixed-class tickets, tight layovers under 60 minutes, and premium cabin indicators.
- **Text & Foreground**:
  - `text-primary`: `#F4F4F5` (High-contrast neutral white)
  - `text-secondary`: `#A1A1AA` (Subdued labels, cabin descriptions, operator meta)
  - `text-muted`: `#71717A` (Inactive airport names, breadcrumb lines, inactive toggle tracks)

## Typography

The type system adopts a dual-font structure:
- **Inter** handles narrative copy, page titles, interactive labels, and general navigation to provide neutral, crystal-clear readability at all viewports.
- **JetBrains Mono** is mandatory for quantitative, temporal, and navigational telemetry. This includes IATA codes (`ATH`, `JFK`, `ZRH`), departure/arrival times (`6:55 AM`), itinerary durations (`12h 55m`), cash/mileage amounts (`$1,191`), and discount calculations (`$3,250 Off Retail`).

Tabular numbers ensure that stacked flight cards line up seamlessly across complex comparison sheets. Numeric labels employ tighter tracking to enhance instrument-like precision.

## Layout & Spacing

The system is constructed around a 4px base rhythm, scaling systematically across standard 8px multiples.

### Grid & Width Constraints
- **Search & Results Container**: Max width constrained to `1200px` to maintain optimal scan lines during dense flight comparisons.
- **Desktop Grid**: 12-column grid with `1.5rem` (24px) gutters and adaptive margins.
- **Flight Card Anatomy**: Employs a continuous multi-column flex layout on desktop:
  - Fare Pill: `110px` fixed width
  - Airline identity: `160px`
  - Origin / Time: `140px`
  - Path visual / Stoppage indicator: Flexible column (min `120px`)
  - Destination / Time: `140px`
  - Duration & Codes: `110px`
  - Badges & Action: Auto right-aligned
- **Mobile Reflow**: Flight cards break into stacked zones. Origin and Destination times pair on top; cabin badge, retail savings pill, and booking trigger stack below with full-bleed touch targets.

## Elevation & Depth

Visual hierarchy does not rely on soft dropshadows. Instead, depth is structured through **Tonal Luminance Stacking** accented with low-intensity perimeter glow:

- **Level 0 (Canvas)**: `#0A0A0B` (Ground plane)
- **Level 1 (Cards & Groups)**: `#141416` with a crisp `1px solid #232328` boundary.
- **Level 2 (Active Flyouts & Overlays)**: `#1C1C1F` with `1px solid #323238` and a soft ambient occlusion shadow `0 12px 32px -4px rgba(0, 0, 0, 0.65)`.
- **Primary Active Glow**: Interactive focused inputs and primary red action elements generate a constrained crimson aura: `box-shadow: 0 0 16px rgba(225, 29, 46, 0.28)`.
- **Itinerary Connecting Paths**: Vertical flight transit routes inside expanded cards use a `1px` dashed line tinted in `#3F3F46`, illuminated at connection checkpoints by `#E11D2E` solid nodes.

## Shapes

The design language favors **Soft (Level 1)** geometry to balance technical efficiency with modern ergonomics:
- Default interactive controls (buttons, text inputs, dropdown selects): `0.375rem` (6px) border radius.
- Cards, flight rows, and search matrix boxes: `0.5rem` (8px) border radius.
- Status badges, discount pills, and segment toggles: `0.25rem` to `0.375rem` (4px–6px) for a structured architectural finish.
- Full round pills (`9999px`) are strictly reserved for micro tag markers, such as the `● Live Data` status pulse indicator.

## Components

### Buttons
- **Primary**: Background `#E11D2E`, text `#FFFFFF`, font `Inter` weight 600. Hover state shifts to `#C31624` with `0 0 12px rgba(225, 29, 46, 0.35)`. Focused via a 2px offset border in `#E11D2E`.
- **Secondary / Ghost**: Background `#1C1C1F`, border `1px solid #2E2E33`, text `#F4F4F5`. Hover elevates background to `#26262B`.
- **Danger / Warning Action**: Translucent background `rgba(225, 29, 46, 0.12)` with `#E11D2E` text label.

### Flight Search Inputs & Form Controls
- **Input Matrix**: Clustered horizontal inputs styled with surface `#141416`, `1px solid #28282D`, and internal padding `0.625rem 0.875rem`. Focus ring triggers a sharp `1px solid #E11D2E`.
- **IATA Chips inside Inputs**: In-field chips (`ATH`, `NYC`) sit on `#222227` with a subtle removal cross (`×`) and `JetBrains Mono` label font.
- **Direction Toggle Swap**: Circular or rounded icon button placed between origin/destination, `#1C1C1F` surface, rotating on interaction.

### Badges & Pill Tags
- **Award Savings Badge**: Background `rgba(34, 197, 94, 0.12)`, border `1px solid rgba(34, 197, 94, 0.25)`, text `#22C55E`, using `label-mono-xs`.
- **Mixed Class / Warning Badge**: Background `rgba(245, 158, 11, 0.12)`, border `1px solid rgba(245, 158, 11, 0.25)`, text `#F59E0B`, displaying cabin split percentages.
- **Flight Class Pill**: Background `#222227`, text `#A1A1AA`, border `1px solid #2F2F36`.

### Flight Result Cards
- **Base Container**: `#141416` surface, border `1px solid #232328`, hover border `#383842`.
- **Price Metric Tile**: Primary fare framed inside `#1C1C1F` with high-visibility mono text (`label-mono-lg`).
- **Path Flightline**: A solid horizontal `1.5px` rule `#3F3F46` interrupted by a directional jet glyph in `#E11D2E`.
- **Expanded Drawer**: Nested panel `#111113` revealing flight legs, plane models (e.g. `Airbus A320-100/200`), cabin code (`Business Class (D)`), and layover warnings connected via an unbroken vertical timeline.

### Selection Tabs & Sort Bars
- Filter bar utilizes horizontal scroll on mobile and a unified pill strip on desktop. Active sort category (`Best`, `Fastest`, `Cheapest`) features an active `#E11D2E` bottom bar or solid `#1C1C1F` container fill with pure white label.