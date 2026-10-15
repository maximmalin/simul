/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        zone: {
          50: '#1a1d1a',
          100: '#1f221f',
          200: '#252925',
          300: '#2d322d',
          400: '#3a403a',
          500: '#4a514a',
          600: '#5e665e',
          700: '#7a827a',
          800: '#a0a8a0',
          900: '#c4ccc4',
        },
        rust: {
          400: '#c4663a',
          500: '#a8542e',
          600: '#8b4424',
          700: '#6d3518',
          800: '#4f2612',
        },
        toxic: {
          300: '#9ed484',
          400: '#7dba68',
          500: '#5e9e4d',
          600: '#4a8039',
          700: '#3a6329',
        },
        blood: {
          500: '#c41e1e',
          600: '#a01818',
          700: '#7d1212',
          800: '#5a0c0c',
          900: '#3d0808',
        },
        amber: {
          400: '#e8a83a',
          500: '#d4922a',
          600: '#b87a1e',
        },
        steel: {
          400: '#6b7280',
          500: '#4a5560',
          600: '#3a4250',
          700: '#2a3038',
        },
      },
      fontFamily: {
        display: ['Oswald', 'sans-serif'],
        body: ['Manrope', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      animation: {
        'scan': 'scan 8s linear infinite',
        'flicker': 'flicker 4s linear infinite',
        'pulse-glow': 'pulse-glow 2.5s ease-in-out infinite',
        'slide-up': 'slide-up 0.4s ease-out forwards',
        'ticker': 'ticker 40s linear infinite',
        'blink': 'blink 1.2s step-end infinite',
        'static-noise': 'static-noise 0.5s steps(4) infinite',
      },
      keyframes: {
        scan: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100vh)' },
        },
        flicker: {
          '0%, 100%': { opacity: '1' },
          '48%': { opacity: '1' },
          '49%': { opacity: '0.85' },
          '50%': { opacity: '1' },
          '52%': { opacity: '0.9' },
          '53%': { opacity: '1' },
        },
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 8px rgba(196,30,30,0.3)' },
          '50%': { boxShadow: '0 0 24px rgba(196,30,30,0.6)' },
        },
        'slide-up': {
          '0%': { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        ticker: {
          '0%': { transform: 'translateX(0)' },
          '100%': { transform: 'translateX(-50%)' },
        },
        blink: {
          '0%, 49%': { opacity: '1' },
          '50%, 100%': { opacity: '0.2' },
        },
        'static-noise': {
          '0%': { opacity: '0.03' },
          '25%': { opacity: '0.05' },
          '50%': { opacity: '0.02' },
          '75%': { opacity: '0.06' },
          '100%': { opacity: '0.03' },
        },
      },
    },
  },
  plugins: [],
};
