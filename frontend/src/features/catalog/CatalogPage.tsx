import {useEffect, useState} from 'react';
import type {FormEvent} from 'react';
import {getCategories, getProducts} from './api';
import {ProductCard} from './ProductCard';
import type {Category, Product} from './types';
import './catalog.css';

export function CatalogPage() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [draftSearch, setDraftSearch] = useState('');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [retry, setRetry] = useState(0);
  const [categoriesLoading, setCategoriesLoading] = useState(true);
  const [productsLoading, setProductsLoading] = useState(true);
  const [categoriesError, setCategoriesError] = useState(false);
  const [productsError, setProductsError] = useState(false);

  useEffect(() => {
    let active = true;
    setCategoriesLoading(true); setCategoriesError(false);
    getCategories().then(data => {if (active) setCategories(data);})
      .catch(() => {if (active) setCategoriesError(true);})
      .finally(() => {if (active) setCategoriesLoading(false);});
    return () => {active = false;};
  }, [retry]);

  useEffect(() => {
    let active = true;
    setProductsLoading(true); setProductsError(false);
    getProducts(search, category).then(data => {if (active) setProducts(data);})
      .catch(() => {if (active) setProductsError(true);})
      .finally(() => {if (active) setProductsLoading(false);});
    // Предыдущий ответ больше не меняет страницу после смены фильтров или ухода.
    return () => {active = false;};
  }, [search, category, retry]);

  function submit(event: FormEvent) {
    event.preventDefault(); setSearch(draftSearch.trim());
  }

  function reset() {
    setDraftSearch(''); setSearch(''); setCategory('');
  }

  const loading = categoriesLoading || productsLoading;
  const error = categoriesError || productsError;
  const categoryNames = new Map(categories.map(item => [item.id, item.name]));

  return <main className="catalog-page">
    <div className="catalog-heading"><p className="eyebrow">ВЕЩИ ДЛЯ ВАШИХ ПЛАНОВ</p><h1>Каталог товаров</h1><p>Найдите то, что нужно для дома, дела и отдыха.</p></div>
    <form className="catalog-filters" onSubmit={submit} aria-label="Поиск и фильтры">
      <label className="search-field">Название товара<input type="search" value={draftSearch} onChange={event => setDraftSearch(event.target.value)} placeholder="Что вы ищете?"/></label>
      <div className="category-field"><label htmlFor="catalog-category">Категория</label><select id="catalog-category" value={category} disabled={categoriesLoading || categoriesError} onChange={event => setCategory(event.target.value)}>
        <option value="">Все категории</option>{categories.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></div>
      <button type="submit">Найти</button><button className="secondary-button" type="button" onClick={reset}>Сбросить</button>
    </form>
    <section className="catalog-results" aria-label="Товары" aria-busy={loading}>
      {error ? <div className="catalog-state error" role="alert"><h2>Не удалось загрузить каталог</h2><p>{categoriesError ? 'Не удалось получить категории.' : 'Не удалось получить товары.'} Проверьте соединение и попробуйте снова.</p><button type="button" onClick={() => setRetry(value => value + 1)}>Повторить запрос</button></div>
        : loading ? <div className="catalog-state" role="status">Загрузка…</div>
        : products.length === 0 ? <div className="catalog-state" role="status"><h2>Товары не найдены</h2><p>Попробуйте другое название или сбросьте поиск и категорию.</p></div>
        : <><p className="results-count" role="status">Найдено товаров: {products.length}</p><div className="product-grid">{products.map(product => <ProductCard key={product.id} product={product} categoryName={categoryNames.get(product.category) ?? 'Категория не указана'}/>)}</div></>}
    </section>
  </main>;
}
