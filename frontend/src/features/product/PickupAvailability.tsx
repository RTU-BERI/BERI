import {useEffect, useState} from 'react';
import {getPickupPoints, getProductAvailability} from './api';
import type {PickupPoint, ProductAvailability} from './types';

type LoadState<T> = {status: 'loading'} | {status: 'error'} | {status: 'ready'; data: T};

export function PickupAvailability({productId}: {productId: number}) {
  const [points, setPoints] = useState<LoadState<PickupPoint[]>>({status: 'loading'});
  const [availability, setAvailability] = useState<LoadState<ProductAvailability>>({status: 'loading'});
  const [selectedId, setSelectedId] = useState('');
  const [pointsRetry, setPointsRetry] = useState(0);
  const [availabilityRetry, setAvailabilityRetry] = useState(0);

  useEffect(() => {
    let active = true;
    setPoints({status: 'loading'});
    getPickupPoints().then(data => {if (active) setPoints({status: 'ready', data});})
      .catch(() => {if (active) setPoints({status: 'error'});});
    return () => {active = false;};
  }, [pointsRetry]);

  useEffect(() => {
    let active = true;
    setAvailability({status: 'loading'});
    getProductAvailability(productId).then(data => {if (active) setAvailability({status: 'ready', data});})
      .catch(() => {if (active) setAvailability({status: 'error'});});
    return () => {active = false;};
  }, [productId, availabilityRetry]);

  const selectedPoint = points.status === 'ready' ? points.data.find(point => String(point.id) === selectedId) : undefined;
  const count = selectedPoint && availability.status === 'ready'
    ? availability.data.pickup_points.find(point => point.pickup_point_id === selectedPoint.id)?.available_count
    : undefined;

  return <section className="product-page__pickup" aria-labelledby="product-pickup-title"
    aria-busy={points.status === 'loading' || availability.status === 'loading'}>
    <h2 id="product-pickup-title">Пункт выдачи и наличие</h2>
    <label htmlFor="product-pickup">Выберите пункт выдачи</label>
    <select id="product-pickup" value={selectedPoint ? selectedId : ''}
      disabled={points.status !== 'ready' || points.data.length === 0}
      onChange={event => setSelectedId(event.target.value)}>
      <option value="">Выберите пункт выдачи</option>
      {points.status === 'ready' && points.data.map(point => <option key={point.id} value={point.id}>{point.name}</option>)}
    </select>
    {points.status === 'loading' && <p role="status" className="product-page__muted">Загрузка пунктов выдачи…</p>}
    {points.status === 'error' && <div className="feedback error" role="alert">
      <p>Не удалось загрузить пункты выдачи.</p><button type="button" onClick={() => setPointsRetry(value => value + 1)}>Повторить загрузку ПВ</button>
    </div>}
    {points.status === 'ready' && points.data.length === 0 && <p role="status" className="product-page__muted">Пункты выдачи пока не добавлены.</p>}
    {selectedPoint && <p className="product-page__address">{selectedPoint.address}</p>}
    {availability.status === 'error' ? <div className="feedback error" role="alert">
      <p>Не удалось загрузить наличие товара.</p><button type="button" onClick={() => setAvailabilityRetry(value => value + 1)}>Повторить загрузку наличия</button>
    </div> : selectedPoint ? <p className="feedback" role="status">
      {availability.status === 'loading' ? 'Загрузка наличия…'
        : count === undefined ? 'Данные о наличии в этом пункте недоступны.'
        : count === 0 ? 'Нет в наличии' : `Свободных экземпляров: ${count}`}
    </p> : points.status === 'ready' && points.data.length > 0
      ? <p className="product-page__muted" role="status">Выберите пункт выдачи, чтобы узнать наличие.</p> : null}
  </section>;
}
