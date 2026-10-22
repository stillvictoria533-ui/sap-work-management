export function setupMotion() {
  const root = document.getElementById("sap-desk-design")!;
  const home = document.getElementById("sd-home-screen")!;
  const journey = document.getElementById("journey")!;
  const element = (selector: string) =>
    home.querySelector<HTMLElement>(selector)!;
  const stage = element(".sd-journey-stage"),
    paper = element(".sd-paper"),
    far = element(".sd-paper-back-far"),
    near = element(".sd-paper-back-near"),
    tab = element(".sd-coral-tab"),
    arch = element(".sd-stage-arch");
  const copies = [...home.querySelectorAll<HTMLElement>("[data-stage-copy]")],
    panels = [...home.querySelectorAll<HTMLElement>("[data-paper-panel]")],
    steps = [...home.querySelectorAll<HTMLElement>("[data-journey-step]")];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)"),
    small = matchMedia("(max-width:680px)");
  let frame = 0,
    active = -1,
    start = 0,
    travel = 1;
  function measure() {
    start =
      scrollY +
      journey.getBoundingClientRect().top -
      parseFloat(getComputedStyle(stage).top);
    travel = Math.max(1, journey.offsetHeight - stage.offsetHeight);
  }
  function paint() {
    frame = 0;
    if (document.hidden) return;
    const p = Math.max(0, Math.min(1, (scrollY - start) / travel)),
      index = p < 0.34 ? 0 : p < 0.68 ? 1 : 2;
    root.dataset.motionStage = String(index + 1);
    if (!reduce.matches) {
      const a = small.matches ? 0.52 : 1;
      paper.style.transform = `translate3d(${(-10 + 20 * p) * a}px,${(8 - 22 * p) * a}px,0) rotate(${-9 + 12 * p}deg) rotateY(${(-7 + 7 * p) * a}deg)`;
      far.style.transform = `translate3d(${(48 - 67 * p) * a}px,${(-6 + 12 * p) * a}px,-60px) rotate(${14 - 24 * p}deg)`;
      near.style.transform = `translate3d(${(21 - 8 * p) * a}px,${(-11 + 16 * p) * a}px,-30px) rotate(${7 - 13 * p}deg)`;
      tab.style.transform = `translate3d(${12 * p * a}px,${(20 - 42 * p) * a}px,0) rotate(${12 - 18 * p}deg)`;
      arch.style.transform = `translate3d(${-42 * p * a}px,${-48 * p * a}px,0) rotate(${-18 + 6 * p}deg)`;
    } else
      [paper, far, near, tab, arch].forEach((el) =>
        el.style.removeProperty("transform"),
      );
    if (index !== active) {
      const direction = index > active ? 1 : -1;
      [copies, panels].forEach((list, g) =>
        list.forEach((el, i) => {
          const chosen = i === index;
          el.getAnimations().forEach((a) => a.cancel());
          el.style.opacity = chosen ? "1" : "0";
          el.style.transform = "none";
          el.setAttribute("aria-hidden", String(!chosen));
          el.inert = !chosen;
          if (chosen && !reduce.matches)
            el.animate(
              [
                {
                  opacity: 0,
                  transform:
                    g === 0
                      ? `translateY(${24 * direction}px)`
                      : `translateX(${33 * direction}px)`,
                },
                { opacity: 1, transform: "none" },
              ],
              { duration: 420, easing: "cubic-bezier(.2,.7,.3,1)" },
            );
        }),
      );
      steps.forEach((b, i) =>
        b.setAttribute("aria-pressed", String(i === index)),
      );
      active = index;
    }
  }
  function schedule() {
    if (!frame) frame = requestAnimationFrame(paint);
  }
  function resize() {
    measure();
    schedule();
  }
  function visibility() {
    if (document.hidden) {
      cancelAnimationFrame(frame);
      frame = 0;
    } else schedule();
  }
  function preference() {
    measure();
    active = -1;
    schedule();
  }
  function click(event: Event) {
    const b = (event.target as HTMLElement).closest<HTMLElement>(
      "[data-journey-step]",
    );
    if (!b) return;
    measure();
    scrollTo({
      top: Math.max(
        0,
        start + travel * [0.08, 0.46, 0.82][Number(b.dataset.journeyStep)]!,
      ),
      behavior: reduce.matches ? "instant" : "smooth",
    });
  }
  addEventListener("scroll", schedule, { passive: true });
  addEventListener("resize", resize, { passive: true });
  document.addEventListener("visibilitychange", visibility);
  reduce.addEventListener("change", preference);
  home.addEventListener("click", click);
  const observer = new ResizeObserver(resize);
  observer.observe(journey);
  measure();
  schedule();
  return () => {
    cancelAnimationFrame(frame);
    removeEventListener("scroll", schedule);
    removeEventListener("resize", resize);
    document.removeEventListener("visibilitychange", visibility);
    reduce.removeEventListener("change", preference);
    home.removeEventListener("click", click);
    observer.disconnect();
  };
}
