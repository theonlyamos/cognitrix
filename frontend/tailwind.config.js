/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    container: { center: true, padding: '2rem', screens: { '2xl': '1400px' } },
    extend: {
      colors: {
        // "Technical / Signal" tokens (CSS-var backed; theme-reactive)
        bg: 'rgb(var(--bg-rgb) / <alpha-value>)',
        panel: 'rgb(var(--panel-rgb) / <alpha-value>)',
        'panel-2': 'rgb(var(--panel-2-rgb) / <alpha-value>)',
        line: 'var(--line)',
        fg: 'rgb(var(--fg-rgb) / <alpha-value>)',
        'fg-dim': 'rgb(var(--fg-dim-rgb) / <alpha-value>)',
        accent: {
          DEFAULT: 'rgb(var(--accent-rgb) / <alpha-value>)',
          foreground: 'rgb(var(--on-accent-rgb) / <alpha-value>)',
          ink: 'rgb(var(--accent-ink-rgb) / <alpha-value>)',
        },
        danger: {
          DEFAULT: 'rgb(var(--danger-rgb) / <alpha-value>)',
          ink: 'rgb(var(--danger-ink-rgb) / <alpha-value>)',
        },
        ok: 'rgb(var(--ok-rgb) / <alpha-value>)',
        // transition aliases so any lingering shadcn/base classes resolve sanely
        border: 'var(--line)',
        input: 'var(--line)',
        ring: 'var(--focus)',
        background: 'rgb(var(--bg-rgb) / <alpha-value>)',
        foreground: 'rgb(var(--fg-rgb) / <alpha-value>)',
        card: 'rgb(var(--panel-rgb) / <alpha-value>)',
        muted: {
          DEFAULT: 'rgb(var(--panel-2-rgb) / <alpha-value>)',
          foreground: 'rgb(var(--fg-dim-rgb) / <alpha-value>)',
        },
        primary: {
          DEFAULT: 'rgb(var(--accent-rgb) / <alpha-value>)',
          foreground: 'rgb(var(--on-accent-rgb) / <alpha-value>)',
        },
        destructive: {
          DEFAULT: 'rgb(var(--danger-rgb) / <alpha-value>)',
          foreground: 'rgb(var(--on-accent-rgb) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['Space Grotesk', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: { sm: 'var(--r1)', DEFAULT: '6px', md: 'var(--r2)', lg: '10px', xl: '12px' },
      boxShadow: {
        ring: '0 0 0 3px color-mix(in oklab, var(--accent) 30%, transparent)',
        panel: '0 12px 40px -12px rgba(0,0,0,.5)',
      },
      keyframes: {
        'accordion-down': { from: { height: 0 }, to: { height: 'var(--radix-accordion-content-height)' } },
        'accordion-up': { from: { height: 'var(--radix-accordion-content-height)' }, to: { height: 0 } },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [],
}
