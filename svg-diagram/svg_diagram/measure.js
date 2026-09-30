// SVG text measurement and wrapping use the same engine as final inspection.
({labels, family}) => {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  document.body.appendChild(svg);
  const text = document.createElementNS(NS, 'text');
  svg.appendChild(text);
  text.setAttribute('font-family', family);
  text.style.whiteSpace = 'pre';
  const words = new Intl.Segmenter(undefined, {granularity: 'word'});
  const graphemes = new Intl.Segmenter(undefined, {granularity: 'grapheme'});
  const result = labels.map(label => {
    const size = label.font_size;
    text.setAttribute('font-size', size);
    text.setAttribute('font-weight', label.weight);
    function metric(content) {
      text.textContent = content;
      const b = text.getBBox();
      const left = Math.min(0, b.x);
      return {kind:'text', content, width:Math.max(text.getComputedTextLength(), b.x+b.width)-left,
        ascent:Math.max(0,-b.y), descent:Math.max(0,b.y+b.height), bearing:left};
    }
    const max = label.max_width || Infinity;
    let current = [], width = 0;
    const lines = [];
    function flush() {
      while (current.length && current.at(-1).kind === 'text' && /^\s+$/.test(current.at(-1).content)) {
        width -= current.pop().width;
      }
      const merged = [];
      for (const run of current) {
        if (run.kind === 'text' && merged.at(-1)?.kind === 'text') {
          merged[merged.length-1] = metric(merged.at(-1).content + run.content);
        } else merged.push(run);
      }
      current = merged;
      width = current.reduce((sum,run)=>sum+run.width,0);
      const ascent = Math.max(size*.8, ...current.map(run=>run.ascent));
      const descent = Math.max(size*.2, ...current.map(run=>run.descent));
      lines.push({runs:current, width, ascent, descent, height:ascent+descent});
      current = []; width = 0;
    }
    function append(run) {
      if (current.length && width+run.width > max) flush();
      if (!current.length && run.kind === 'text' && /^\s+$/.test(run.content)) return;
      current.push(run); width += run.width;
    }
    for (const run of label.runs) {
      if (run.kind === 'math') { append(run); continue; }
      const paragraphs = run.content.split('\n');
      paragraphs.forEach((paragraph, index) => {
        if (index) flush();
        for (const {segment} of words.segment(paragraph)) {
          const part = metric(segment);
          if (part.width > max) {
            for (const {segment:char} of graphemes.segment(segment)) append(metric(char));
          } else append(part);
        }
      });
    }
    if (current.length || !lines.length) flush();
    const gap = size*.25;
    return {lines, width:Math.max(...lines.map(line=>line.width)),
      height:lines.reduce((sum,line)=>sum+line.height,0)+gap*(lines.length-1), gap,
      overflow:lines.some(line=>line.width > max+.1)};
  });
  svg.remove();
  return result;
}
