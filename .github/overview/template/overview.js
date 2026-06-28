// overview.js — включает pan/zoom на inline-SVG диаграммах через вендоренный svg-pan-zoom.
// Кнопки +/−/reset, колесо — зум, тащить — двигать, тач/пинч. Клики по узлам ($link) сохраняются.
// Если svg-pan-zoom не загрузился — тихо оставляем статичный SVG.
(function () {
  if (typeof window.svgPanZoom !== "function") return;
  document.querySelectorAll(".diagram svg").forEach(function (svg) {
    // PlantUML пишет в SVG жёсткие пиксельные размеры (style="width:NNNpx" + width/height)
    // и preserveAspectRatio="none". Инлайн-style перебивает CSS, и svg-pan-zoom считает
    // канвас равным натуральной ширине диаграммы — fit вырождается. Снимаем пиксельную
    // фиксацию и чиним соотношение сторон; viewBox у PlantUML уже есть (если нет — строим).
    svg.style.removeProperty("width");
    svg.style.removeProperty("height");
    if (!svg.getAttribute("viewBox")) {
      try {
        var b = svg.getBBox();
        if (b.width && b.height) {
          svg.setAttribute("viewBox", b.x + " " + b.y + " " + b.width + " " + b.height);
        }
      } catch (e) { /* SVG ещё не отрисован — оставляем как есть */ }
    }
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.parentElement.classList.add("pannable");
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", "100%");
    window.svgPanZoom(svg, {
      zoomEnabled: true,
      controlIconsEnabled: true, // экранные кнопки +/−/reset
      fit: true,
      center: true,
      minZoom: 0.2,
      maxZoom: 20,
    });
  });
})();
