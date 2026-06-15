/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        mantle:  '#00D4AA',
        'mantle-dim': '#00a884',
        dark:    '#0A0E1A',
        card:    '#0F1629',
        'card-hover': '#141c35',
        border:  '#1a2744',
        text:    '#C9D1D9',
        muted:   '#6b7280',
        bull:    '#00D4AA',
        bear:    '#FF6B6B',
        caution: '#FFD700',
        neutral: '#6b7280',
      },
      fontFamily: {
        sans:  ['Inter', 'system-ui', 'sans-serif'],
        mono:  ['Space Mono', 'JetBrains Mono', 'monospace'],
      },
      animation: {
        'glow-pulse':  'glowPulse 2s ease-in-out infinite',
        'price-flash': 'priceFlash 0.5s ease-out',
        'slide-up':    'slideUp 0.4s ease-out',
        'fade-in':     'fadeIn 0.3s ease-out',
      },
      keyframes: {
        glowPulse: {
          '0%, 100%': { boxShadow: '0 0 8px rgba(0,212,170,0.3)' },
          '50%':       { boxShadow: '0 0 24px rgba(0,212,170,0.6)' },
        },
        priceFlash: {
          '0%':   { backgroundColor: 'rgba(0,212,170,0.3)' },
          '100%': { backgroundColor: 'transparent' },
        },
        slideUp: {
          '0%':   { opacity: 0, transform: 'translateY(16px)' },
          '100%': { opacity: 1, transform: 'translateY(0)' },
        },
        fadeIn: {
          '0%':   { opacity: 0 },
          '100%': { opacity: 1 },
        },
      },
      backdropBlur: { xs: '2px' },
    },
  },
  plugins: [],
}
