import { Compiler } from "inkjs/full";
import { defineConfig, type Plugin } from "vite";

/**
 * Lets the game import `.ink` files (dialogue, written in Ink: see
 * src/dialogue.ts). Each is compiled here, as the game is built or served,
 * into the JSON the Ink runtime plays, so the page ships the small runtime
 * and never the compiler; a mistake in a story stops the build with the line
 * it is on.
 */
function ink(): Plugin {
  return {
    name: "ink",
    transform(source, id) {
      if (!id.endsWith(".ink")) return null;
      const problems: string[] = [];
      try {
        const story = new Compiler(source, { errorHandler: (message: string) => problems.push(message) } as never).Compile();
        if (problems.some((problem) => /ERROR/i.test(problem))) throw new Error(problems.join("\n"));
        for (const problem of problems) this.warn(problem);
        return { code: `export default ${JSON.stringify(story.ToJson())};`, map: null };
      } catch (error) {
        this.error(`${id}\n${problems.join("\n") || (error as Error).message}`);
      }
    },
  };
}

export default defineConfig({
  plugins: [ink()],
  build: { target: "es2022", chunkSizeWarningLimit: 6000 },
});
