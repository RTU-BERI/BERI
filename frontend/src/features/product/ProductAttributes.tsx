import {formatDecimal} from './format';
import type {ProductAttribute} from './types';

function attributeValue(attribute: ProductAttribute): string {
  if (attribute.value_boolean !== null) return attribute.value_boolean ? 'Да' : 'Нет';
  if (attribute.value_number !== null) return formatDecimal(attribute.value_number);
  return attribute.value_text?.trim() ? attribute.value_text : 'Не указано';
}

export function ProductAttributes({attributes}: {attributes: ProductAttribute[]}) {
  return <section className="product-page__panel" aria-labelledby="product-attributes-title">
    <h2 id="product-attributes-title">Характеристики</h2>
    {attributes.length === 0 ? <p className="product-page__muted">Характеристики пока не указаны.</p>
      : <dl className="product-page__attributes">{attributes.map((attribute, index) => <div key={index}>
        <dt>{attribute.name}</dt><dd>{attributeValue(attribute)}{attribute.unit ? ` ${attribute.unit}` : ''}</dd>
      </div>)}</dl>}
  </section>;
}
