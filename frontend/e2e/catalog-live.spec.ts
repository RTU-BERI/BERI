import {expect, test} from '@playwright/test';
import type {Category, Product} from '../src/features/catalog/types';

// Без page.route: нужен настоящий Django API и непустая собственная demo-БД.
test('real API: public catalog, search, category, combination, empty and reset', async ({page, request, context}) => {
  const categoryResponse = await request.get('/api/categories/');
  const productResponse = await request.get('/api/products/');
  expect(categoryResponse.status()).toBe(200);
  expect(productResponse.status()).toBe(200);
  const categories: Category[] = await categoryResponse.json();
  const products: Product[] = await productResponse.json();
  expect(Array.isArray(categories)).toBeTruthy();
  expect(Array.isArray(products)).toBeTruthy();
  expect(products.length, 'Добавьте тестовые товары в собственной demo-БД').toBeGreaterThan(0);
  const product = products[0];
  const category = categories.find(item => item.id === product.category)!;
  expect(category).toBeDefined();
  await page.goto('/catalog');
  await expect(page.getByRole('article')).toHaveCount(products.length);
  await page.getByLabel('Название товара').fill(product.name);
  await page.getByLabel('Название товара').press('Enter');
  const matching = products.filter(item => item.name.toLowerCase().includes(product.name.toLowerCase()));
  await expect(page.getByRole('article')).toHaveCount(matching.length);
  await page.getByLabel('Категория', {exact: true}).selectOption(String(category.id));
  await expect(page.getByRole('article')).toHaveCount(matching.filter(item => item.category === category.id).length);
  await page.getByRole('button', {name: 'Сбросить'}).click();
  await expect(page.getByRole('article')).toHaveCount(products.length);
  await page.getByLabel('Категория', {exact: true}).selectOption(String(category.id));
  await expect(page.getByRole('article')).toHaveCount(products.filter(item => item.category === category.id).length);
  await page.getByLabel('Название товара').fill('s1-p5-no-match-8f2d5d');
  await page.getByRole('button', {name: 'Найти', exact: true}).click();
  await expect(page.getByText('Товары не найдены', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Сбросить'}).click();
  await expect(page.getByRole('article')).toHaveCount(products.length);
  expect((await context.cookies()).some(cookie => cookie.name === 'sessionid')).toBeFalsy();
  await page.screenshot({path: 'test-results/catalog-live-desktop.png', fullPage: true});
});
