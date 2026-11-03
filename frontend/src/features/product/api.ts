import {api, ApiError} from '../../shared/api/client';
import type {PickupPoint, ProductAttribute, ProductAvailability, ProductDetail} from './types';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const isId = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
const isNullableString = (value: unknown) => value === null || typeof value === 'string';
const isDecimal = (value: unknown) => typeof value === 'string' && /^-?\d+(?:\.\d+)?$/.test(value);

function contractError(): never {
  throw new ApiError('invalid_product_response', 'Сервер вернул неожиданный формат данных. Попробуйте позже.');
}

function isAttribute(value: unknown): value is ProductAttribute {
  return isRecord(value) && typeof value.name === 'string' && isNullableString(value.unit)
    && isNullableString(value.value_text) && (value.value_number === null || isDecimal(value.value_number))
    && (value.value_boolean === null || typeof value.value_boolean === 'boolean')
    && [value.value_text, value.value_number, value.value_boolean].filter(item => item !== null).length === 1;
}

export async function getProduct(id: number): Promise<ProductDetail> {
  const data = await api<unknown>(`/api/products/${id}/`);
  if (!isRecord(data) || data.id !== id || typeof data.name !== 'string' || typeof data.status !== 'string'
    || !isNullableString(data.description) || !isNullableString(data.image_url)
    || !isNullableString(data.brand) || !isNullableString(data.model)
    || !isRecord(data.category) || !isId(data.category.id) || typeof data.category.name !== 'string'
    || !Array.isArray(data.attributes) || !data.attributes.every(isAttribute)
    || !Array.isArray(data.components) || !data.components.every(item => isRecord(item)
      && typeof item.name === 'string' && isId(item.quantity))) contractError();
  const tariff = data.tariff;
  if (tariff !== null && (!isRecord(tariff) || typeof tariff.hourly_rate !== 'string'
    || !/^\d+(?:\.\d+)?$/.test(tariff.hourly_rate) || !/[1-9]/.test(tariff.hourly_rate)
    || typeof tariff.valid_from !== 'string' || !isNullableString(tariff.valid_to))) contractError();
  return data as ProductDetail;
}

export async function getPickupPoints(): Promise<PickupPoint[]> {
  const data = await api<unknown>('/api/pickup-points/');
  if (!Array.isArray(data) || !data.every(item => isRecord(item) && isId(item.id)
    && typeof item.name === 'string' && typeof item.address === 'string' && typeof item.status === 'string')
    || new Set(data.map(item => item.id)).size !== data.length) contractError();
  return data as PickupPoint[];
}

export async function getProductAvailability(id: number): Promise<ProductAvailability> {
  const data = await api<unknown>(`/api/products/${id}/availability/`);
  if (!isRecord(data) || data.product_id !== id || !Array.isArray(data.pickup_points)
    || !data.pickup_points.every(item => isRecord(item) && isId(item.pickup_point_id)
      && typeof item.available_count === 'number' && Number.isSafeInteger(item.available_count) && item.available_count >= 0)
    || new Set(data.pickup_points.map(item => item.pickup_point_id)).size !== data.pickup_points.length) contractError();
  return data as ProductAvailability;
}
