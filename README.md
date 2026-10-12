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

## Tela de testes do TCC

A tela `/tests` é uma ferramenta independente para os experimentos e avaliações do TCC. Ela não faz parte do fluxo do usuário final e não é aberta automaticamente junto com o sistema.

Para utilizá-la, inicie normalmente o Smart Parking e depois execute `ABRIR_TESTES.bat` ou acesse diretamente `http://127.0.0.1:5000/tests`.

O dashboard e o editor de configuração não possuem links para a tela de testes. Os testes servem exclusivamente para analisar vídeos, comparar as detecções com um gabarito manual e obter métricas para a avaliação acadêmica.
