/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    screens: { xs: '420px', sm: '640px', md: '768px', lg: '1024px', xl: '1280px', '2xl': '1536px' },
    extend: {
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'system-ui', '-apple-system', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'Cascadia Code', 'Consolas', 'monospace'],
      },
      colors: {
        brand: {
          50: '#F1F1FA', 100: '#E2E1F3', 200: '#C4C2E7', 300: '#9B98D3', 400: '#6E6AB8', 500: '#4B479A',
          600: '#3A3683', 700: '#2B296E', 800: '#211F57', 900: '#17163E', 950: '#0E0D27',
        },
        olive: {
          50: '#F6F8EA', 100: '#ECF1D3', 200: '#D9E3A8', 300: '#C0D06F', 400: '#A9BE4C', 500: '#97AC3B',
          600: '#7A8C2C', 700: '#5D6B24', 800: '#4A5522', 900: '#3F4820',
        },
        accent: { 400: '#38BDF8', 500: '#0EA5E9', 600: '#0284C7' },
        surface: 'rgb(var(--surface) / <alpha-value>)',
        'surface-2': 'rgb(var(--surface-2) / <alpha-value>)',
        canvas: 'rgb(var(--canvas) / <alpha-value>)',
        line: 'rgb(var(--line) / <alpha-value>)',
        ink: 'rgb(var(--ink) / <alpha-value>)',
        muted: 'rgb(var(--muted) / <alpha-value>)',
      },
      boxShadow: {
        card: '0 1px 2px rgb(16 24 40 / 0.04), 0 1px 3px rgb(16 24 40 / 0.06)',
        pop: '0 12px 32px -8px rgb(16 24 40 / 0.18), 0 4px 8px -4px rgb(16 24 40 / 0.08)',
      },
      borderRadius: { xl: '0.875rem', '2xl': '1.125rem' },
      keyframes: { shimmer: { '100%': { transform: 'translateX(100%)' } } },
      animation: { shimmer: 'shimmer 1.4s infinite' },
    },
  },
  plugins: [],
};
