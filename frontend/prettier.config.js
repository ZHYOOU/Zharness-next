/**
 * @see https://prettier.io/docs/configuration
 * Prettier configuration.
 *
 * Prettier 配置。
 * @type {import("prettier").Config}
 */
const config = {
  endOfLine: "auto",
  singleAttributePerLine: true,
  plugins: ["prettier-plugin-tailwindcss"],
};

export default config;
