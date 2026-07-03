/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    container: { center: true, padding: '2rem', screens: { '2xl': '1400px' } },
    extend: {
      colors: {
        // "Technical / Signal" tokens (CSS-var backed; theme-reactive)
        bg: 'var(--bg)',
        panel: 'var(--panel)',
        'panel-2': 'var(--panel-2)',
        line: 'var(--line)',
        fg: 'var(--fg)',
        'fg-dim': 'var(--fg-dim)',
        accent: { DEFAULT: 'var(--accent)', foreground: 'var(--on-accent)', ink: 'var(--accent-ink)' },
        danger: { DEFAULT: 'var(--danger)', ink: 'var(--danger-ink)' },
        ok: 'var(--ok)',
        // transition aliases so any lingering shadcn/base classes resolve sanely
        border: 'var(--line)',
        input: 'var(--line)',
        ring: 'var(--accent)',
        background: 'var(--bg)',
        foreground: 'var(--fg)',
        card: 'var(--panel)',
        muted: { DEFAULT: 'var(--panel-2)', foreground: 'var(--fg-dim)' },
        primary: { DEFAULT: 'var(--accent)', foreground: 'var(--on-accent)' },
        destructive: { DEFAULT: 'var(--danger)', foreground: 'var(--on-accent)' },
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
