import {api, ApiError} from '../../shared/api/client';
import type {Category, Product} from './types';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

const isNullableString = (value: unknown) => value === null || typeof value === 'string';
const isId = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value > 0;

function contractError(): never {
  throw new ApiError('invalid_catalog_response', 'Сервер вернул неожиданный формат каталога. Попробуйте позже.');
}

export async function getCategories(): Promise<Category[]> {
  const data = await api<unknown>('/api/categories/');
  if (!Array.isArray(data) || !data.every(item => isRecord(item) && isId(item.id) && typeof item.name === 'string')) contractError();
  return data as Category[];
}

export async function getProducts(search: string, category: string): Promise<Product[]> {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  if (category) params.set('category', category);
  const query = params.toString();
  const data = await api<unknown>(`/api/products/${query ? `?${query}` : ''}`);
  if (!Array.isArray(data) || !data.every(item => isRecord(item) && isId(item.id)
    && typeof item.name === 'string' && isId(item.category) && typeof item.status === 'string'
    && isNullableString(item.image_url) && isNullableString(item.brand) && isNullableString(item.model))) contractError();
  return data as Product[];
}
