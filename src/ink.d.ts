/** An Ink story, compiled to the JSON its runtime plays (the `ink` plugin in vite.config.ts does it). */
declare module "*.ink" {
  const compiled: string;
  export default compiled;
}
