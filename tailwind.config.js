/** @type {import('tailwindcss').Config} */
module.exports = {
    content: ["./web/templates/*.html"],
    theme: {
	extend: {
	    fontFamily: {
		sans: ["Iosevka Aile Iaso", "sans-serif"],
		mono: ["Iosevka Curly Iaso", "monospace"],
		serif: ["Iosevka Etoile Iaso", "serif"],
	    },
	},
    },
    plugins: [],
};
