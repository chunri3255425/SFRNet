/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        'moire-red': '#d32f2f',
        'moire-green': '#2e7d32',
        'moire-blue': '#1565c0',
      },
    },
  },
  plugins: [],
};
