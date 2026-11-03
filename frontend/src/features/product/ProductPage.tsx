import {useEffect, useState} from 'react';
import {Link, useParams} from 'react-router-dom';
import {ApiError} from '../../shared/api/client';
import {getProduct} from './api';
import {formatDecimal} from './format';
import {PickupAvailability} from './PickupAvailability';
import {ProductAttributes} from './ProductAttributes';
import {ProductComponents} from './ProductComponents';
import {ProductImage} from './ProductImage';
import type {ProductDetail} from './types';
import './product.css';

type ProductState = {status: 'loading'} | {status: 'ready'; data: ProductDetail} | {status: 'error'; notFound: boolean};

function NotFound() {
  return <div className="catalog-state"><h1>Товар не найден</h1><p>Возможно, ссылка устарела. Выберите другой товар в каталоге.</p>
    <p><Link to="/catalog">Вернуться в каталог</Link></p>
  </div>;
}

function ProductContent({id}: {id: number}) {
  const [state, setState] = useState<ProductState>({status: 'loading'});
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    setState({status: 'loading'});
    getProduct(id).then(data => {if (active) setState({status: 'ready', data});})
      .catch(error => {if (active) setState({status: 'error', notFound: error instanceof ApiError && error.status === 404});});
    return () => {active = false;};
  }, [id, retry]);

  if (state.status === 'loading') return <div className="catalog-state" role="status">Загрузка товара…</div>;
  if (state.status === 'error') return state.notFound ? <NotFound/> : <div className="catalog-state error" role="alert">
    <h1>Не удалось загрузить товар</h1><p>Проверьте соединение и попробуйте снова.</p>
    <button type="button" onClick={() => setRetry(value => value + 1)}>Повторить загрузку товара</button>
    <p><Link to="/catalog">Вернуться в каталог</Link></p>
  </div>;

  const product = state.data;
  return <>
    <div className="product-page__summary">
      <ProductImage image={product.image_url} name={product.name}/>
      <div className="product-page__panel">
        <p className="product-category">{product.category.name}</p><h1>{product.name}</h1>
        {(product.brand || product.model) && <p className="product-model">{[product.brand, product.model].filter(Boolean).join(' · ')}</p>}
        <section className="product-page__description" aria-labelledby="product-description-title">
          <h2 id="product-description-title">Описание</h2>
          <p>{product.description?.trim() ? product.description : 'Описание пока не добавлено.'}</p>
        </section>
        <section className="product-page__tariff" aria-labelledby="product-tariff-title">
          <h2 id="product-tariff-title">Стоимость аренды</h2>
          {product.tariff ? <p className="product-page__price">{formatDecimal(product.tariff.hourly_rate, false)} <span>₽/час</span></p>
            : <p className="product-page__muted">Стоимость аренды сейчас недоступна.</p>}
        </section>
        <PickupAvailability key={product.id} productId={product.id}/>
      </div>
    </div>
    <div className="product-page__sections">
      <ProductAttributes attributes={product.attributes}/><ProductComponents components={product.components}/>
    </div>
  </>;
}

export function ProductPage() {
  const {id = ''} = useParams();
  const productId = /^[1-9]\d*$/.test(id) ? Number(id) : NaN;
  return <main className="product-page">
    <div className="product-page__back"><Link to="/catalog">← В каталог</Link></div>
    {Number.isSafeInteger(productId) ? <ProductContent key={id} id={productId}/> : <NotFound/>}
  </main>;
}
