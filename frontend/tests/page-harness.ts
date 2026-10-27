// Mount real SFC setup code with Vue's renderer and an in-memory router. No DOM
// emulation or duplicated page logic; API/state dependencies are explicit fakes.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import vm from "node:vm";
import { parse, compileScript } from "@vue/compiler-sfc";
import ts from "typescript";
import { createRenderer, h, nextTick } from "vue";
import { createRouter, createMemoryHistory, RouterView } from "vue-router";
import * as VueRouter from "vue-router";

const require = createRequire(import.meta.url);
export async function flush() {
  for (let i = 0; i < 8; i++)
    await new Promise<void>((done) => setImmediate(done));
  await nextTick();
}
export function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
export async function mountPage(
  name: string,
  mocks: Record<string, unknown>,
  path = "/tickets/1",
) {
  const cache = new Map<string, unknown>();
  function load(filename: string): any {
    if (cache.has(filename)) return cache.get(filename);
    const source = readFileSync(filename, "utf8");
    const code = filename.endsWith(".vue")
      ? compileScript(parse(source, { filename }).descriptor, { id: name })
          .content
      : source;
    const output = ts.transpileModule(code, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText;
    const module = { exports: {} as any };
    const localRequire = (id: string) => {
      if (id in mocks) return mocks[id];
      if (id === "vue-router") return VueRouter;
      if (id.endsWith(".vue")) return { default: { render: () => null } };
      return id.startsWith(".")
        ? load(resolve(dirname(filename), id + ".ts"))
        : require(id);
    };
    vm.runInThisContext(`(function(require,module,exports){${output}\n})`, {
      filename,
    })(localRequire, module, module.exports);
    cache.set(filename, module.exports);
    return module.exports;
  }
  const component = load(
    resolve(import.meta.dirname, "../src/pages", name + ".vue"),
  ).default;
  component.render = () => null;
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/tickets/:id", component },
      { path: "/notifications", component },
      { path: "/operations", component },
      { path: "/tickets", component: { render: () => null } },
    ],
  });
  const node = () => ({ parent: null as any });
  const renderer = createRenderer<any, any>({
    createElement: node,
    createText: node,
    createComment: node,
    insert(child, parent) {
      child.parent = parent;
    },
    remove() {},
    parentNode: (n) => n.parent,
    nextSibling: () => null,
    setText() {},
    setElementText() {},
    patchProp() {},
  });
  let instance: any;
  const app = renderer.createApp({
    render: () =>
      h(
        RouterView,
        {},
        {
          default: ({ Component }: any) =>
            Component &&
            h(Component, {
              ref: (value: any) => {
                instance = value;
              },
            }),
        },
      ),
  });
  app.use(router);
  await router.push(path);
  app.mount(node());
  await router.isReady();
  await flush();
  return {
    router,
    state: () => instance.$.setupState,
    unmount: () => app.unmount(),
  };
}
