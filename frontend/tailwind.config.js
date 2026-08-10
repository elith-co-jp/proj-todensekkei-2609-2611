/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // proj-koa の tailwind.config.js と同じ KOA テーマ
        koa: {
          50: '#f0f5fb', 100: '#dbe8f4', 200: '#b8d1e9', 300: '#84b0d8',
          400: '#4a88c1', 500: '#0055a4', 600: '#00498e', 700: '#003c75',
          800: '#002f5c', 900: '#001f3d',
        },
        'koa-green': {
          50: '#f0f7f4', 100: '#dceee4', 500: '#3f9067',
          600: '#2f7352', 700: '#265f44', 800: '#1d4a36',
        },
      },
    },
  },
  plugins: [],
}
