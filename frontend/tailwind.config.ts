import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "#070707",
        panel: "#12100d",
        mint: "#79d29f",
        amber: "#e0b45d",
        coral: "#ff7d6b",
        skyline: "#efc972"
      },
      boxShadow: {
        panel: "0 32px 58px -42px rgba(0, 0, 0, 0.86)"
      }
    }
  },
  plugins: []
};

export default config;
