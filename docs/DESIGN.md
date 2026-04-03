# DESIGN.md — Design System Reference

## Brand Identity

**Brand Name:** קאפה ושות׳ (Capa & Co.)
**Brand Voice:** Premium but approachable. Professional but warm. Like a trusted business partner, not a corporate vendor.
**Visual Style:** Minimal, earthy, boutique-feel. Think specialty coffee branding meets artisan food.

---

## Color System

### Primary Palette

```
Brand Green     #4A5D3A    — Primary CTAs, accents, selection highlight
Green Soft      #8FA878    — Stats numbers, secondary accents
Cream           #FAF7F2    — Page background, light text on dark
Dark            #1A1A18    — Primary text, dark backgrounds
Dark Surface    #2A2A26    — Section gradient backgrounds
```

### Functional Colors

```
WhatsApp Green  #25D366    — WhatsApp buttons, FAB
Gray Text       #6A6A68    — Secondary body text
```

### Opacity Patterns (on dark backgrounds)

```
rgba(250,247,242, 0.92)   — Nav background (glassmorphism)
rgba(250,247,242, 0.55)   — Subtitle text on dark
rgba(250,247,242, 0.5)    — Form labels
rgba(250,247,242, 0.4)    — Footer text
rgba(250,247,242, 0.3)    — "Or" divider text
rgba(250,247,242, 0.25)   — Copyright text
rgba(250,247,242, 0.12)   — Input borders on dark
rgba(250,247,242, 0.1)    — Card borders, dividers on dark
rgba(250,247,242, 0.06)   — Input backgrounds on dark
rgba(250,247,242, 0.05)   — Form card background

rgba(26,26,24, 0.08)      — Light border (nav bottom)
rgba(74,93,58, 0.2)       — Step numbers (green muted)
rgba(74,93,58, 0.06)      — Value card background (green tint)
```

---

## Typography

### Font Stack

```css
/* Hebrew Serif (titles, logo, display) */
font-family: 'Frank Ruhl Libre', 'Playfair Display', serif;

/* Hebrew Sans (body, UI, buttons) */
font-family: 'Heebo', 'DM Sans', sans-serif;
```

### Type Scale

| Role          | Font             | Size    | Weight  | Line Height | Letter Spacing  |
| ------------- | ---------------- | ------- | ------- | ----------- | --------------- |
| Hero Title L1 | Frank Ruhl Libre | 48px    | 700     | 1.1         | -1px            |
| Hero Title L2 | Frank Ruhl Libre | 56px    | 700     | 1.1         | -1px            |
| Hero Italic   | Playfair Display | inherit | italic  | —           | —               |
| Section Title | Frank Ruhl Libre | 36px    | 700     | 1.2         | -0.5px          |
| Section Label | Heebo            | 13px    | 500     | —           | 2px (uppercase) |
| Value Title   | Heebo            | 15px    | 600     | —           | —               |
| Step Number   | Playfair Display | 32px    | 300     | —           | —               |
| Step Title    | Heebo            | 17px    | 600     | —           | —               |
| Body Text     | Heebo            | 15-16px | 300     | 1.7-1.8     | —               |
| Small/Labels  | Heebo            | 13px    | 400-500 | —           | —               |
| Nav Links     | Heebo            | 14px    | 400     | —           | —               |
| Logo          | Frank Ruhl Libre | 22px    | 700     | —           | -0.5px          |

---

## Spacing System

```
Section padding:     80px vertical, 32px horizontal
Content max-width:   800px (sections), 1100px (nav)
Form max-width:      560px
Card padding:        24px
Cell gaps:           16-28px
Button padding:      16px 32px (primary), 12px 24px (secondary)
Input padding:       12px 14px
```

---

## Component Patterns

### Buttons

```
Primary:     bg #4A5D3A, text #FAF7F2, border-radius 6px, weight 600
WhatsApp:    bg #25D366, text white, border-radius 6px, with icon
Secondary:   transparent bg, border 1px rgba cream, text cream, border-radius 20px
Disabled:    opacity 0.5
Hover:       opacity transition 0.3s
```

### Cards

```
Value Card:   bg rgba(74,93,58,0.06), border 1px rgba(74,93,58,0.12), radius 10px, padding 24px
Step Card:    bg white, border 1px rgba(26,26,24,0.06), radius 10px, padding 28px
Form Card:    bg rgba(250,247,242,0.05), border 1px rgba(250,247,242,0.1), radius 12px, padding 32px
```

### Form Inputs

```
Background:    rgba(250,247,242, 0.06)
Border:        1px solid rgba(250,247,242, 0.12)
Border Focus:  1px solid #4A5D3A
Border Radius: 6px
Text Color:    #FAF7F2
Direction:     rtl (ltr for phone/email)
```

---

## Animation Specs

### Scroll Reveal (IntersectionObserver)

- Trigger: 15% visibility threshold
- Transform: `translateY(32px)` → `translateY(0)`
- Opacity: `0` → `1`
- Duration: `0.7s ease`
- Stagger: `0.12-0.15s` between sibling elements

### Keyframe Animations

```css
@keyframes slideDown    { from: opacity 0, translateY(-20px) → to: opacity 1, translateY(0) }
@keyframes float        { 0%,100%: translateY(0) — 50%: translateY(-6px) }
@keyframes fadeInScale  { from: opacity 0, scale(0.95) → to: opacity 1, scale(1) }
@keyframes gentlePulse  { 0%,100%: opacity 0.6 — 50%: opacity 1 }
```

---

## Responsive Considerations (TODO)

Currently the site uses flex-wrap and max-widths for basic responsiveness. Planned breakpoints:

```
Desktop:  > 1024px  — Current design (2-col grids, side-by-side)
Tablet:   768-1024  — Reduce padding, adjust font sizes
Mobile:   < 768px   — Single column, hamburger menu, stacked CTAs
```

### Mobile-specific needs:

- Hamburger menu for navigation
- Single-column form fields
- Full-width buttons
- Reduced hero title size
- Stacked proof banner stats (2×2 grid)
