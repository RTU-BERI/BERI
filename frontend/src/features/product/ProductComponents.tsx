import type {ProductComponent} from './types';

export function ProductComponents({components}: {components: ProductComponent[]}) {
  if (components.length === 0) return null;
  return <section className="product-page__panel" aria-labelledby="product-components-title">
    <h2 id="product-components-title">Комплектация</h2>
    <ul className="product-page__components">{components.map((component, index) =>
      <li key={index}><span>{component.name}</span><span>{component.quantity} шт.</span></li>)}</ul>
  </section>;
}
