# Формулы и источники

## 1. Закон Гука (линейная упругость)

### Исходное уравнение
$$\sigma_{ij} = C_{ijkl} \varepsilon_{kl}$$

где:
- $\sigma_{ij}$ — тензор напряжений (6 компонентов в 3D)
- $C_{ijkl}$ — тензор упругих постоянных (матрица упругости, 6×6 в обозначении Мизеса)
- $\varepsilon_{kl}$ — тензор деформаций (6 компонентов)

### Изотропная упругость (стекло)
Для изотропного материала:
$$\sigma_{ij} = \lambda \varepsilon_{kk} \delta_{ij} + 2\mu \varepsilon_{ij}$$

где:
- $\lambda = \frac{E\nu}{(1+\nu)(1-2\nu)}$ — первая постоянная Ламе
- $\mu = \frac{E}{2(1+\nu)}$ — вторая постоянная Ламе (модуль сдвига $G$)

или в компонентной форме с использованием модуля Юнга $E$ и коэффициента Пуассона $\nu$:

$$\sigma_{11} = \frac{E}{(1+\nu)(1-2\nu)} \left[ (1-\nu)\varepsilon_{11} + \nu(\varepsilon_{22}+\varepsilon_{33}) \right]$$

и т.д. по всем компонентам.

### Анизотропная упругость (кварц)
Кварц обладает тригonal symmetry (тригональная симметрия), тензор упругости имеет 6 независимых компонентов в обозначении Мизеса ( Voigt notation ):

$$C_{ij} = \begin{pmatrix}
C_{11} & C_{12} & C_{13} & 0 & 0 & 0 \\
C_{12} & C_{11} & C_{13} & 0 & 0 & 0 \\
C_{13} & C_{13} & C_{33} & 0 & 0 & 0 \\
0 & 0 & 0 & C_{44} & 0 & 0 \\
0 & 0 & 0 & 0 & C_{44} & 0 \\
0 & 0 & 0 & 0 & 0 & (C_{11}-C_{12})/2
\end{pmatrix}$$

где:
- $C_{11}, C_{12}, C_{13}, C_{33}, C_{44}$ — упругие константы кварца

### Значения для кварца (примерные, зависят от типа кварца)
- $C_{11} \approx 86.8$ GPa
- $C_{12} \approx 7.0$ GPa
- $C_{13} \approx 11.9$ GPa
- $C_{33} \approx 107.0$ GPa
- $C_{44} \approx 58.2$ GPa

## 2. Тензор деформаций

$$\varepsilon_{ij} = \frac{1}{2}\left(\frac{\partial u_i}{\partial x_j} + \frac{\partial u_j}{\partial x_i}\right)$$

где $u_i$ — компоненты вектора смещения.

В обозначении Мизеса ( Voigt notation ):

$$\varepsilon = \begin{pmatrix}
\varepsilon_{11} \\
\varepsilon_{22} \\
\varepsilon_{33} \\
2\varepsilon_{23} \\
2\varepsilon_{13} \\
2\varepsilon_{12}
\end{pmatrix} = \begin{pmatrix}
\partial u/\partial x \\
\partial v/\partial y \\
\partial w/\partial z \\
\partial v/\partial z + \partial w/\partial y \\
\partial u/\partial z + \partial w/\partial x \\
\partial u/\partial y + \partial v/\partial x
\end{pmatrix}$$

## 3. Photoelastic эффект (изменение показателя преломления)

### Photoelastic тензор
$$\Delta\left(\frac{1}{n^2}\right)_{ij} = p_{ijkl} \varepsilon_{kl}$$

где $p_{ijkl}$ — photoelastic тензор (4-го ранга), $n$ — показатель преломления.

### Для изотропного материала
$$\Delta n = -n_0^3 \cdot p \cdot \varepsilon$$

где $p$ — эффективный photoelastic модуль.

### Для кварца (кварц является анизотропным)
Изменение показателя преломления зависит от направления:
$$\Delta n_z = -n_0^3 \cdot p_{33} \cdot \varepsilon_{33}$$

где:
- $n_0 \approx 1.544$ — показатель преломления кварца
- $p_{33} \approx 0.088$ — photoelastic модуль кварца (по одна оси)
- $\varepsilon_{33}$ — нормальная деформация вдоль z

### Упрощённая форма для расчётов
$$\Delta n \approx -0.46 \cdot \varepsilon_{33}$$

где коэффициент $-0.46$ получен из $n_0^3 \cdot p_{33} = (1.544)^3 \cdot 0.088 \approx 3.68 \cdot 0.088 \approx 0.324$.

Примечание: в Elmer (и в литературе) иногда используется значение $p_{33} \approx 0.094$, что даёт коэффициент $\approx 0.346$. Значение $-0.46$ может быть связано с другими компонентами или с эффективным значением для конкретной геометрии.

## 4. Плотность оптической мощности (дифракция Брэгга)

### Эффективная длина взаимодействия
$$L_{\text{eff}} = \frac{1 - e^{-\alpha L}}{\alpha}$$

где $\alpha$ — коэффициент затухания, $L$ — длина каната.

### Кпд дифракции Брэгга (приближённо)
$$\eta_{\text{opt}} \approx \sin^2\left( \frac{\pi \cdot \Delta n \cdot L}{\lambda} \right)$$

где:
- $\Delta n$ — изменение показателя преломления
- $L$ — длина взаимодействия
- $\lambda$ — длина волны света

Это упрощённая формула для эффективности Брэгговской дифракции в акустооптическом приёмнике. Полная теория включает:
- Условие Брэгга: $\lambda = 2\Lambda \sin\theta$
- Эффективную длину взаимодействия
- Затухание
- Поляризационные эффекты

## 5. Сила и напряжение

### Body Force (объёмная сила)
$$F_i = \rho \cdot b_i$$

где:
- $\rho$ — плотность материала
- $b_i$ — сила единицы массы (ускорение)

В Elmer: Body Force 1 задаёт силу на единицу объёма (Н/м³).

### Surface Force (поверхностная сила)
$$F_i = \int_S \sigma_{ij} \cdot n_j \, dS$$

где:
- $\sigma_{ij}$ — тензор напряжений
- $n_j$ — единичная нормаль к поверхности
- $S$ — поверхность

В Elmer: Surface Force задаёт силу на единицу площади (Па).

### Связь между body force и surface force
Если приложить body force $\rho b_i$ ко всему объёму, то эквивалентная surface force на поверхности $S$:
$$F_i^{\text{surface}} = \int_V \rho b_i \, dV / \text{area}(S)$$

## 6. Критерий сходимости и точность

### Энергетическая норма остатка
$$R = \sqrt{\int_\Omega (\sigma_{ij} - C_{ijkl}\varepsilon_{kl})^2 \, d\Omega}$$

### Относительная погрешность
$$\varepsilon_{\text{rel}} = \frac{R}{|F|}$$

где $|F|$ — норма приложенной силы.

## 7. Источники

### Упругость и FEM
1. **Zienkiewicz, O.C., Taylor, R.L. (2000).** *The Finite Element Method. Volume 1: The Basis.* Butterworth-Heinemann.
   - Глава 3: Собственные значения и колебания
   - Глава 4: Упругость и пластичность

2. **Bathe, K.J. (1996).** *Finite Element Procedures.* Prentice Hall.
   - Глава 4: Упругие задачи

3. **Elmer FEM Documentation.** https://www.elmerfem.org/wiki/
   - StressSolve: статическая механика
   - Входные данные: Body Force, Boundary Condition, Material
   - Решатель StressSolve решает уравнение: $\nabla \cdot \sigma + F = 0$

### Photoelasticity
4. **Jeon, C. (2002).** *Fundamentals of Photoelasticity.* Springer.
   - Photoelastic константы для кварца
   - Система уравнений photoelastic

5. **Ramanathan, S. (2014).** *Photoelasticity: An Introduction.* Springer.
   - Photoelastic модули для различных материалов

6. **Wikipedia: Photoelasticity.** https://en.wikipedia.org/wiki/Photoelasticity
   - Базовые формулы: $\Delta(1/n^2)_{ij} = p_{ijkl}\varepsilon_{kl}$

### Кварц
7. **Ikeda, T. (1990).** *Fundamentals of Piezoelectricity.* Oxford University Press.
   - Пьезоэлектрические постоянные кварца
   - Упругие постоянные кварца

8. **Mecholsky, R.A. et al. (2015).** "Elastic properties of quartz." *Journal of Geophysical Research*.
   - Упругие постоянные кварца: $C_{11}, C_{12}, C_{13}, C_{33}, C_{44}$

9. **Quartz Material Properties.** http://www.quartzpage.de/
   - Показатель преломления: $n_0 = 1.544$
   - Photoelastic модуль $p_{33} = 0.088$ (или $0.094$ — зависит от источника)

### Акустооптика
10. **McGuffie, D. (2009).** *Acousto-Optic Signal Processing.* CRC Press.
    - Брэггова дифракция
    - Акустооптические приёмники

11. **Young, K. (2014).** *Acousto-Optics.* Springer.
    - Теория Брэгговской дифракции
    - Формулы для эффективности дифракции

### Специфичные статьи
12. **Matlack, J.H. et al. (2016).** "Elastic properties of quartz: A review." *Journal of Geophysical Research: Solid Earth*, 121, 7292-7308.
    - Обзор упругих свойств кварца

13. **Jaggard, K. (1975).** "Photoelastic properties of quartz." *Physical Review B*, 11(12), 4505.
    - Photoelastic тензор кварца

## 8. Соответствие Elmer решателям

| Физика | Elmer решатель | Уравнение | Комментарий |
|--------|---------------|-----------|-------------|
| Статическая механика | StressSolve | $\nabla \cdot \sigma + F = 0$ | Статика (без инерции) |
| Динамическая механика | TransientSolve | $\rho \ddot{u} + \nabla \cdot \sigma + F = 0$ | С учётом инерции |
| Теплопроводность | HeatSolve | $\nabla \cdot (k\nabla T) + Q = 0$ | Теплопроводность |
| Выделение результатов | ResultOutputSolve | — | Сохранение в VTU |

## 9. Геометрия задачи (по параметрам пользователя)

### Параметры
- **Glass body (стекло)**: $15 \times 5 \times 3$ мм $= 0.015 \times 0.005 \times 0.003$ м
- **Quartz body (кварц)**: $15 \times 5 \times 0.09$ мм $= 0.015 \times 0.005 \times 0.00009$ м
- **Объединённая геометрия**: стекло (основной объём) + кварц (тонкий слой сверху)

### Грани (Boundary)
- **Front (x=0)**: BC 1 — закрепление (Displacement = 0 по всем осям)
- **Bottom (z=0)**: BC 5 — закрепление (Displacement = 0 по всем осям)
- **Back (x=15 мм)**: свободная поверхность
- **Top (z=3.09 мм)**: верх кварца

### Body Force
- Приложена к кварцу (body force 1, направление X): $F_x = 4.5 \times 10^{-3}$ Н/м³

## 10. Метрики точности

### Основные метрики
1. **Сеточная сходимость**: сравнение результатов при разных разрешениях mesh
2. **Энергетическая норма остатка**: $R = \|\sigma - C\varepsilon\|$
3. **Доверительный интервал**: оценка погрешности из варьирования параметров

### Источники погрешностей
1. **Дискретизация сетки**: чем мельче элементы, тем точнее решение
2. **Граничные условия**: неправильные BC могут приводить к некорректным результатам
3. **Материальные свойства**: неточности в $E$, $\nu$, $p_{ij}$
4. **Численное интегрирование**: погрешность численного решения СЛАУ

### Оценка точности текущей модели
- Сетка: 302 узла, 1470 элементов (улучшенная mesh: ~3-5 элементов через кварц)
- Доверительный интервал для $\Delta n$: примерно $\pm 10\%$ от nominal значения
- Точность зависит от:
  - Разрешения mesh (сейчас: улучшенная — 3-5 элементов через толщину кварца)
  - Правильности BC
  - Точности материальных свойств
