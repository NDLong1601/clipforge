export function productValues(project) {
  const source = project.assets.filter((asset) => asset.role === 'source' && !asset.deleted).at(-1);
  const info =
    source && project.product_info?.source_id === source.id ? project.product_info : null;
  const name =
    project.product_name?.trim() ||
    info?.name?.trim() ||
    source?.name
      .replace(/\.[^.]+$/, '')
      .replaceAll('_', ' ')
      .trim() ||
    project.name;
  return {
    product_name: name,
    product_description:
      project.product_description?.trim() ||
      project.script.trim() ||
      info?.description?.trim() ||
      source?.tags?.trim() ||
      '',
    cta: project.product_cta ?? 'KHÁM PHÁ SẢN PHẨM',
  };
}

export function layerText(project, layer, clip) {
  const values = productValues(project);
  const aliases = {
    'TÊN SẢN PHẨM': 'product_name',
    'MÔ TẢ SẢN PHẨM': 'product_description',
    'KHÁM PHÁ SẢN PHẨM': 'cta',
  };
  const content =
    layer.content && layer.content !== 'static'
      ? layer.content
      : aliases[layer.text.trim().toUpperCase()];
  if (content) return values[content];
  let text = layer.text;
  for (const [key, value] of Object.entries({
    ...values,
    title: clip.title,
    project: project.name,
    caption: clip.caption,
  }))
    text = text.replaceAll(`{${key}}`, value);
  return text;
}
