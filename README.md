# GestaoEstacionamento
Sistema de monitoramento de vagas de estacionamento baseado em visão computacional, com suporte a câmeras 360° e configuração adaptável dos ambientes por meio da definição manual das áreas de estacionamento.

## Avaliação experimental

A tela **Testes** permite executar um ambiente em diferentes vídeos/fontes. O mesmo conjunto de vagas pode ser avaliado contra outra gravação, e também é possível selecionar um vídeo diretamente pelo navegador.

Após o processamento, cada amostra é salva em `test_results/<ambiente>_<id>/samples/` e os resultados automáticos ficam em `data.csv`. O teste também salva `metadata.json` com a configuração utilizada.

### Gabarito manual

Na tela de revisão, cada amostra apresenta a imagem original e o estado detectado pelo YOLO para cada vaga. O operador marca manualmente **Ocupada** ou **Livre**. As marcações ficam em `ground_truth.json` e podem ser corrigidas posteriormente.

### Métricas

A partir do gabarito são calculados:

- TP, FP, TN e FN;
- acurácia;
- precisão;
- recall;
- taxa de falsos positivos;
- quantidade de acertos e erros.

As métricas são calculadas por vaga e no conjunto completo e ficam disponíveis em `metrics.json` e `metrics.csv`.

### Movimento da câmera e câmeras 360°/panorâmicas

A estabilização é opcional por ambiente (editor de ROI → "Câmera/vídeo em movimento") e pode ser forçada por teste. O sistema casa pontos ORB do frame atual com o frame de referência do ambiente e estima o movimento da câmera com o **modelo adequado ao tipo de imagem**, escolhido em "Tipo de câmera / imagem":

| Tipo | Modelo de movimento | Observação |
|---|---|---|
| Convencional (perspectiva) | Homografia + RANSAC (fallback afim) | Exato para câmera girando no próprio eixo |
| 360° equirretangular (2:1) | Rotação 3D (Kabsch + RANSAC) sobre a esfera | Trata vagas que cruzam a emenda esquerda/direita |
| 360° olho de peixe | Rotação 3D, modelo equidistante | Informe o FOV da lente (ex.: 180°–220°) |
| Panorâmica cilíndrica | Rotação 3D, modelo cilíndrico | Informe o FOV horizontal |

Nas imagens 360° e panorâmicas, a rotação da câmera não é uma homografia: um giro horizontal faz a cena "dar a volta" na emenda e inclinações curvam as retas. Por isso cada pixel é convertido em um raio 3D (`camera_models.py`), a rotação é estimada entre os raios e as vagas (com as arestas subdivididas, para poderem se curvar) são reprojetadas.

Etapas adicionais (`roi_stabilizer.py`):

- **Refinamento local por vaga**: mediana do erro residual dos pontos próximos de cada vaga, compensando paralaxe e FOV aproximado.
- **Suavização adaptativa**: amortece o tremor, mas segue sem atraso movimentos reais da câmera.
- **Rastreamento encadeado**: se o frame não casar com a referência (luz, borrão), casa com o último frame bem rastreado e compõe as transformações.
- **Máscara do nadir na 360°**: ignora os 12% inferiores (tripé/suporte, que se move junto com a câmera). Ajustável por `mask_bottom` em `__detection__`.
- **Vagas fora de vista**: se a câmera girar e menos de 50% da vaga ficar no quadro, ela é marcada como fora de vista (dashboard: `visible: false`; testes: `fora_de_vista`, excluída das métricas) em vez de ser reportada como livre.
- **Resolução diferente**: o vídeo pode ter resolução diferente do frame de referência; as coordenadas são escaladas.

Em validação com rotações sintéticas conhecidas (imagem 3840×1920), o erro máximo dos vértices das vagas ficou abaixo de 2 px na projeção equirretangular, inclusive com giro de 170° cruzando a emenda; a homografia original errava entre 76 e 328 px nos mesmos casos.

Os testes registram por amostra `tracking_estavel`, `match_count`, `inliers`, `modo_tracking` (`referencia`, `encadeado`, `mantido`), `movimento_camera` (graus ou pixels) e `vagas_fora_de_vista`.

**Limitações**: o modelo assume que a câmera gira/balança no suporte; deslocamentos grandes da câmera no espaço geram paralaxe apenas parcialmente compensada. A estabilização não remove a distorção da imagem para o YOLO. Vídeo 360° "duplo" (duas lentes lado a lado, sem costura) não é suportado: exporte em equirretangular.

### Diagnóstico da estabilização

```
python diagnostico_estabilizacao.py <ambiente> [--projection equirectangular] [--fov 190] [--step 2] [--show]
```

Roda apenas o rastreamento (sem YOLO) e salva em `diagnosticos/` um vídeo com as vagas na posição estimada e um CSV frame a frame. Útil para escolher a projeção correta e para comparar projeções nos resultados do TCC.

## Protocolo de testes do TCC (equivalente a Rocha, 2025)

A rota oficial para os experimentos é a tela **Testes** (`ABRIR_TESTES.bat` ou `http://127.0.0.1:5000/tests`). Ela processa um vídeo gravado, salva cada amostra e permite montar o gabarito manual sobre exatamente os mesmos quadros. A coleta pelo dashboard (`evaluation_capture.py`) continua existindo, mas depende do monitoramento ao vivo e não é a recomendada para o TCC.

### Parâmetros para reproduzir Rocha (2025)

| Parâmetro | Valor |
|---|---|
| Modelo | `yolo12x.pt`, classe 2 (car) do COCO |
| Confiança | 0,40 |
| Amostragem | **a cada 5 segundos de vídeo** (amostras em 0 s, 5 s, 10 s...) |
| Unidade de análise | vaga × amostra (1 hora de vídeo com 18 vagas = 720 × 18 = 12.960 observações) |
| Rastreamento | desligado (cada amostra é analisada de forma independente) |
| Critério de ocupação | sobreposição ≥ 15% (sistema) e, para comparação, centro da caixa (RegionCounter usado por Rocha) |

### Passo a passo

Tudo é feito na tela de experimentos (`ABRIR_TESTES.bat`), em quatro etapas:

1. **Vídeo**: escolha um vídeo da pasta `data/` ou envie um do computador (fica em `data/videos_teste/`).
2. **Vagas**: escolha um ambiente ou crie um novo e clique em *Definir vagas sobre este vídeo*. No modal, escolha um quadro do vídeo, clique nos cantos de cada vaga (Enter, duplo clique ou clique no primeiro ponto fecham a vaga) e salve. O quadro escolhido vira a referência do ambiente, então as vagas ficam na mesma resolução do vídeo. Vídeos da mesma câmera e ângulo podem usar o mesmo ambiente.
3. **Executar**: dê um nome ao teste, mantenha 5 s de intervalo e informe se o vídeo está acelerado (2×, 4×...), para o intervalo continuar sendo de 5 s reais. Clique em *Iniciar teste*.
4. **Análise**: para cada amostra, clique nas vagas da imagem (ou teclas 1–9) até ficarem com o estado real e aperte Enter para salvar e avançar. A próxima amostra vem pré-preenchida; corrija só o que mudou. A linha do tempo mostra o que falta (cinza) e onde o YOLO errou (vermelho). Deixe *Mostrar o que o YOLO previu* desligado durante a anotação.

No botão **Histórico e tabelas do TCC**, marque os testes na ordem dos vídeos e clique em *Gerar tabelas e figuras do TCC*: é baixado um .zip (também salvo em `resultados_tcc/`) com as Tabelas 1, 4, 5, 6 e 8 em CSV e LaTeX, métricas por vaga e as matrizes de confusão. O mesmo pode ser feito pelo terminal com `python gerar_resultados_tcc.py --listar` e `python gerar_resultados_tcc.py <id1> <id2>`.

### Arquivos de cada teste (`test_results/<ambiente>_<id>/`)

- `metadata.json`: parâmetros, FPS, resolução, duração, critério, tempos (média/desvio por quadro e de inferência) e hardware/software usados.
- `data.csv`: previsão por vaga em cada amostra, com `tempo_total_ms`, `tempo_inferencia_ms` e `num_veiculos`.
- `samples/` e `rois.jsonl`: imagens das amostras (sem desenhos) e a posição das vagas usada em cada uma.
- `ground_truth.json`, `metrics.json`, `metrics.csv`: gabarito e métricas.

### Convenção das métricas

Classe positiva = **vaga ocupada** (TP = ocupada prevista como ocupada; FP = livre prevista como ocupada). As colunas `*_livre` trazem as mesmas métricas com **vaga livre** como positiva. Pela matriz publicada por Rocha (2025) (TP = 16.141, FN = 2.330, FP = 0, TN = 3.561), a precisão de 60,45% corresponde à convenção *livre positiva* (3.561 / 5.891); com *ocupada positiva*, os mesmos dados dão precisão 100%, recall 87,39%, FPR 0% e acurácia 89,42%. As métricas de Rocha (2025) recalculadas aparecem como referência em `metrics.json` e na tela de revisão.

O tempo do primeiro quadro (carregamento/aquecimento do modelo) fica fora da média de tempo por quadro.
