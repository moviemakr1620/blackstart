/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["\"IBM Plex Sans\"", "ui-sans-serif", "system-ui"],
        mono: ["\"IBM Plex Mono\"", "ui-monospace", "SFMono-Regular"],
      },
      colors: {
        ink: {
          950: "#07090d",
          900: "#0c1016",
          800: "#121821",
          700: "#1a222e",
          600: "#243040",
        },
        mint: "#5ce1c5",
        amber: "#f5b942",
        rose: "#ff5c7a",
        sky: "#6cb6ff",
      },
      boxShadow: {
        glow: "0 0 40px rgba(92, 225, 197, 0.12)",
      },
    },
  },
  plugins: [],
};
