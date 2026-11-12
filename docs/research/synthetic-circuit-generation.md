# 制御回路・リレー回路の合成図面生成基盤 調査

- 調査日: 2026-11-12
- 対象: LTspice、ngspice、KiCad、CircuiTikZ、Schemdraw
- 目的: macOS/Linux/headless 環境で、コードから制御回路・リレー回路を定義し、PDF/画像と機械可読 ground truth を同時かつ大量に再現生成する方式を選定する
- 情報源: ベンダー／プロジェクトの公式サイト、公式マニュアル、公式ソースリポジトリのみ
- 注意: ライセンス評価は技術選定上の一次スクリーニングであり、最終的な法的判断ではない

## 結論

**推奨は「独自の正規化回路モデル（JSON/Python）を唯一の正本とし、Schemdraw を主レンダラー、ngspice を任意の電気的検証器として使う」構成である。** KiCad は相互運用確認用の副出力、CircuiTikZ は描画ドメインを増やす第2レンダラーとして段階的に加える。LTspice は参照・手動比較用途に限定し、合成データ生成基盤の中核には採用しない。

この構成なら、同じ正本から次を決定的に生成できる。

1. PDF、SVG、PNG
2. 記号・端子・配線・分岐点・文字列の座標付き ground truth JSON
3. 接続グラフとリレーコイル―接点関係
4. 必要なケースだけ ngspice netlist とシミュレーション期待値
5. seed、生成器バージョン、依存バージョン、出力 SHA-256 を含む再現情報

Schemdraw は公式に headless サーバー向けの `Agg` または SVG backend を案内し、Matplotlib backend から SVG/EPS/PNG/PDF/JPG を保存できる。Relay、各種 switch、breaker、bus、custom element、および element の anchor/bounding box API もあるため、今回必要な制御回路記号と座標 ground truth を同じ Python オブジェクトから生成しやすい。ライセンスは MIT である。公式根拠: [headless と保存形式](https://schemdraw.readthedocs.io/en/stable/usage/start.html)、[Relay](https://schemdraw.readthedocs.io/en/stable/elements/compound.html)、[switch 類](https://schemdraw.readthedocs.io/en/stable/elements/electrical.html)、[breaker/bus等の追加履歴](https://schemdraw.readthedocs.io/en/stable/changes.html)、[custom element](https://schemdraw.readthedocs.io/en/stable/usage/customizing.html)、[anchor/bounding box API](https://schemdraw.readthedocs.io/en/stable/classes/drawing.html)、[MIT License](https://github.com/cdelker/schemdraw/blob/master/LICENSE.txt)。

## 選定基準

| 基準 | 必須条件 |
|---|---|
| 実行環境 | macOS と Linux の双方で非対話・headless 実行できる |
| 大量生成 | 1回の CI job で seed を変えながら多数ページを生成できる |
| 再現性 | 同一入力・同一依存バージョンで同一トポロジーと座標を再生成できる |
| 視覚出力 | PDF と学習・評価用画像を生成できる |
| ground truth | 記号、bbox、端子、配線 polyline、junction、文字、接続関係を推論せずに出力できる |
| 制御回路表現 | relay coil、NO/NC contact、switch、lamp、breaker、bus、junction、端子台、行先札を拡張できる |
| CI適合 | GUI操作、OS固有印刷ダイアログ、手動エクスポートを必要としない |
| ライセンス | CI利用と生成物配布の条件を追跡でき、顧客図面を取り込まず独自生成できる |

## 比較表

| 候補 | macOS/Linux | headless PDF/画像 | 機械可読接続 | GT座標の生成 | ライセンス観点 | 判定 |
|---|---|---|---|---|---|---|
| Schemdraw | Python 3.9+。公式に headless 手順あり | PDF/PNG/SVG等。SVG backendも利用可 | 独自モデルから同時生成 | anchor/bbox と独自配置情報から直接生成しやすい | MIT | **主レンダラーに採用** |
| ngspice | Linux/macOSでビルド・実行可能 | 回路図レンダラーではない | SPICE netlist と batch simulation に強い | 2D座標を持たない | 主に modified BSD、一部例外あり | **任意の論理検証器に採用** |
| KiCad | Windows/macOS/Linux | CLIで schematic PDF/SVG、netlist、ERC | `.kicad_sch` と export netlist | 可能だが形式が大きくUUID管理も必要 | GPLv3+。公式ライブラリは例外付きCC-BY-SA 4.0 | **副出力に限定** |
| CircuiTikZ | TeXが動く環境 | LaTeXからvector PDF。PNGは別途変換 | netlist機能なし | 独自モデルから別途出力が必要 | LPPL 1.3.1 または GPL | **第2レンダラー候補** |
| LTspice | 公式配布はWindows/macOS。Linuxなし | batch simulation/netlistはあるが公式CLIにPDF exportなし | SPICE netlistは取得可能 | `.asc/.asy`直書き仕様は未公開・非推奨 | 独自EULA。用途制限の確認が必要 | **中核には不採用** |

表の根拠は以下の各節に記す。

## 1. Analog Devices LTspice

### 確認できたこと

- LTspice は schematic capture、SPICE simulator、waveform viewer を備える。公式ダウンロード対象は Windows と macOS で、Linux 版は掲載されていない。[Analog Devices LTspice製品ページ](https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html)
- 公式 quick reference は `-b` を `-run`、`-netlist`、`-sync` の batch mode とし、`-run` で schematic の実行、`-netlist` で schematic から netlist への変換を定義している。[Analog Devices SPICE Quick Reference](https://www.analog.com/media/en/news-marketing-collateral/solutions-bulletins-brochures/ltspice-keyboard-shortcuts.pdf)
- 同じ公式コマンドライン一覧には PDF、SVG、PNG の schematic export flag がない。したがって、少なくとも公式に文書化されたCLIだけでは、headless PDF生成経路を構成できない。[Analog Devices SPICE Quick Reference](https://www.analog.com/media/en/news-marketing-collateral/solutions-bulletins-brochures/ltspice-keyboard-shortcuts.pdf)
- schematic は `.asc`、symbol は `.asy` を使うが、Analog Devices社員の公式回答はこれらの直接編集を強く非推奨とし、直接編集用のユーザー文書はないとしている。[Analog Devices EngineerZone: Syntax definition of .asy and .asc files](https://ez.analog.com/design-tools-and-calculators/ltspice/f/q-a/596907/syntax-definition-of-asy-and-asc-files)
- LTspiceは schematic から netlist を生成し、`.net`、`.cir`、`.sp` のテキストnetlistを開いて実行できる。[Analog Devices公式LTspice資料](https://ltspice.analog.com/LTspiceSafari.pdf)
- EULA本文はインストール後の Help 内に置かれる。公式サポート回答では半導体メーカーの特定用途に制限があるため、一般的な「無料」という表示だけで再配布・自動化用途の安全性を判断すべきではない。[EULAの公式所在](https://support.analog.com/en-US/knowledgebase/article/ka-16982)、[Analog Devices社員による用途制限の説明](https://ez.analog.com/design-tools-and-calculators/ltspice/f/q-a/592959/ltspice-license/560421)

### 評価

batch simulation と netlist生成は可能だが、Linux非対応、headless PDF経路の欠如、`.asc/.asy` の生成仕様が未公開かつ直接編集非推奨である。大量の視覚バリエーションをCIで再現生成する主基盤としては不適切である。既存LTspice回路との相互確認やアナログ挙動のスポット確認には使えるが、合成図面生成の正本にはしない。

### 判定

**採用しない（中核用途）。** 手動比較または限定的な参照シミュレーションにのみ残す。

## 2. ngspice

### 確認できたこと

- ngspice公式マニュアルは、回路をnetlistから読み込み、`ngspice -b -r rawfile.raw circuitfile.cir` で非対話のbatch実行を行う方法を定義している。`.ac`、`.tran` 等の解析指示は入力ファイル内に置ける。[ngspice User's Manual](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf)
- 公式サイトは、ソースが Linux、Windows、macOS、BSD、Solaris 等でコンパイル可能とし、Linux/macOS向けの実行経路も案内している。[ngspice Downloads](https://ngspice.sourceforge.io/download.html)
- ngspiceコードは例外を除き modified BSD で、tclspice/numparam等には別ライセンスがある。採用時は利用する構成と配布物のライセンス一覧を固定する必要がある。[ngspice Developers Information](https://ngspice.sourceforge.io/devel.html)、[ngspice User's Manualのライセンス節](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf)
- ngspiceの公式処理フローはnetlistの読込み、回路構造生成、行列計算、解析、結果処理であり、schematicレイアウトやPDF生成を目的とするツールではない。[ngspice Documentation](https://ngspice.sourceforge.io/docs.html)

### 評価

netlistは接続とデバイスの機械可読な正解に向くが、記号bbox、配線polyline、junctionの描画位置、文字位置を持たない。従って単独では画像ground truthを作れない。一方、正規化モデルからngspice netlistも出力すれば、リレー接点を制御switch等へ写像した小規模ケースで、通電状態・導通・期待ノード値の追加検証ができる。

### 判定

**任意の意味検証器として採用。** 描画器にはしない。すべての産業制御記号を無理にSPICEモデル化せず、電気的検証が有効な回路族だけに適用する。

## 3. KiCad

### 確認できたこと

- KiCadはWindows、macOS、Linuxで動作するクロスプラットフォームEDAである。[KiCad About](https://www.kicad.org/about/kicad/)、[KiCad Download](https://www.kicad.org/download/)
- KiCad 10.0の公式CLIは `kicad-cli sch export pdf`、`sch export svg`、`sch export netlist`、`sch erc` を提供する。PDFは各sheetをページとして出力し、netlistはKiCad S-expression/XML、SPICE等へ出力できる。[KiCad 10.0 CLI](https://docs.kicad.org/10.0/en/cli/cli.html)
- `.kicad_sch` は文書化されたS-expression形式で、symbol、junction、wire/bus、text、label、hierarchical sheet等を保持する。第三者生成器は `eeschema` 以外のgenerator名を使うよう明記されている。[KiCad Schematic File Format](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/)
- schematic形式にはUUID、埋込みlibrary symbol、instance path等があり、単純なnetlistより生成・保守範囲が大きい。[KiCad Schematic File Format](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/)
- KiCad本体の大部分はGPLv3+である。公式libraryはCC-BY-SA 4.0だが、公式例外によりlibrary dataを使ったdesignやgenerated fileを同ライセンスにする義務は課されない。一方、library collection自体を再配布する場合は例外対象外である。[KiCad Licenses](https://www.kicad.org/about/licenses/)、[KiCad Libraries License](https://www.kicad.org/libraries/license/index.html)

### 評価

PDFとnetlistを同じEDA sourceから公式CLIで生成できる点は強い。ただし、大量のランダム図面を生成するには完全な`.kicad_sch`、UUID、symbol定義、instance情報を安定して書き出す必要があり、Python描画器より実装・バージョン追随コストが高い。KiCad自身をCI imageへ含めるため実行環境も重くなる。

### 判定

**主基盤には採用しない。副出力として採用候補。** 正規化モデルから一部ケースを`.kicad_sch`へ変換し、`kicad-cli sch erc`、PDF、SVG、SPICE netlistを生成する相互運用テストに使う。生成器をKiCad形式へ直接密結合させない。

## 4. CircuiTikZ

### 確認できたこと

- CircuiTikZはTikZ/LaTeX上で回路をコード記述するpackageで、抵抗などを座標間のpathとして描く。公式manualには最小コード例と、relay相当の `relais` を含む記号一覧がある。[CircuiTikZ Manual](https://circuitikz.github.io/circuitikz/circuitikzmanualgit.pdf)
- packageはLaTeX Project Public License 1.3.1またはGNU Public Licenseで利用できる。[CircuiTikZ Manual: License](https://circuitikz.github.io/circuitikz/circuitikzmanualgit.pdf)
- 公式サイトはversioned release、style、manualを公開しているため、TeX依存を固定して再現ビルドできる。[CircuiTikZ Releases](https://circuitikz.github.io/)

### 評価

ベクタPDF品質と座標制御は優秀で、別レンダリングドメインの生成に向く。ただし、TeX source自体は接続ground truthの正本ではなく、netlistやrelay coil―contact関係も自動では得られない。正規化モデルからTeXとJSONを別々に出すadapterが必要で、TeX Live一式とfontの固定も必要になる。

### 判定

**第2レンダラーとして条件付き採用。** Schemdrawで基盤を成立させた後、線種、font、記号形状、PDF rendererのドメイン差を増やすために導入する。正本や唯一のrendererにはしない。

## 5. Schemdraw

### 確認できたこと

- SchemdrawはPythonで回路記号を配置するAPIを持ち、Python 3.9以上で動作する。[Schemdraw Getting Started](https://schemdraw.readthedocs.io/en/stable/usage/start.html)
- headless serverではMatplotlibの`Agg` backend、またはSchemdrawのSVG backendを使える。Matplotlib backendはSVG、EPS、PNG、PDF、JPG、SVG backendはSVGを保存できる。[Schemdraw Headless Servers / Saving Drawings](https://schemdraw.readthedocs.io/en/stable/usage/start.html)
- RelayはSPDT/DPST/DPDT等の構成を描け、basic elementにはbutton、NO/NC switch、reed switch等がある。[Schemdraw Relay](https://schemdraw.readthedocs.io/en/stable/elements/compound.html)、[Schemdraw Switches](https://schemdraw.readthedocs.io/en/stable/elements/electrical.html)
- custom elementはline、polygon、circle、arc、text、Bezier等のsegmentで定義できるため、今回固有の端子台、行先札、コイル、接点、黒丸、母線記号を追加できる。[Schemdraw Customizing Elements](https://schemdraw.readthedocs.io/en/stable/usage/customizing.html)
- elementはabsolute anchorとbounding boxを取得できる。従って、描画後に画像から再推定せず、配置オブジェクトから端子座標とbboxをground truthへ変換できる。[Schemdraw Element API](https://schemdraw.readthedocs.io/en/stable/classes/drawing.html)
- 公式repositoryはMIT Licenseである。[Schemdraw LICENSE](https://github.com/cdelker/schemdraw/blob/master/LICENSE.txt)

### 評価

既存リポジトリと同じPythonで生成・検証器を実装でき、headless PDF/画像、独自記号、座標取得、軽いCI依存、MITという要件を最も直接満たす。なおSchemdraw自体はnetlist/電気意味モデルではないため、描画コマンドを正本にしてはいけない。正本は独自の正規化回路モデルとし、Schemdrawは純粋なrenderer adapterにする。

### 判定

**主レンダラーとして採用。**

## 推奨アーキテクチャ

```text
seed + generation profile
          |
          v
canonical circuit model (JSON/Python dataclass)
  - semantic topology
  - relay coil/contact linkage
  - symbol/layout constraints
  - style/degradation parameters
          |
          +--> Schemdraw renderer --> SVG/PDF/PNG
          |          |
          |          +--> rendered geometry manifest
          |
          +--> ground-truth exporter --> JSON/JSONL
          |
          +--> ngspice adapter (eligible cases only) --> .cir/.raw/checks
          |
          +--> optional adapters --> CircuiTikZ / KiCad
```

### 正本モデルに最低限持たせる情報

```json
{
  "schema_version": "1.0",
  "generator_version": "git-sha-or-release",
  "seed": 12345,
  "page": {"width": 1755, "height": 1241, "unit": "px"},
  "symbols": [
    {
      "id": "K1_COIL",
      "kind": "relay_coil",
      "label": "K1",
      "bbox": [100, 200, 160, 240],
      "terminals": {"A1": [100, 220], "A2": [160, 220]},
      "state_model": {"energized_by": "NET_CTRL"}
    }
  ],
  "wires": [
    {"id": "W1", "points": [[20, 220], [100, 220]], "net": "NET_CTRL"}
  ],
  "junctions": [{"id": "J1", "point": [60, 220], "radius": 3}],
  "relations": [
    {"type": "coil_controls_contact", "from": "K1_COIL", "to": "K1_CONTACT_1"}
  ],
  "texts": [{"text": "ER21291へ", "bbox": [1300, 200, 1394, 247]}],
  "outputs": {"pdf_sha256": "...", "png_sha256": "..."}
}
```

ground truthのbbox、terminal、wire polyline、junctionはrendererが使用した最終座標から出力する。画像解析結果からground truthを逆算してはならない。Schemdrawのabsolute anchorとbounding box APIを利用できる根拠は[公式Element API](https://schemdraw.readthedocs.io/en/stable/classes/drawing.html)にある。

### 生成する図面族

1. ラダー型制御回路: 母線、横配線、NO/NC接点、relay coil、self-hold、interlock
2. 分岐重点型: 細い母線、低コントラスト黒丸、T字・十字・重なり、dotあり/なし
3. 表示系: lamp、pilot lamp、relay coilとの近接、類似円形記号
4. 保護・端子系: breaker、fuse、terminal block、destination tag
5. 複数ページ・行先参照: sheet reference、同一label、ページ跨ぎ接続
6. 劣化変種: line width、gray level、font、rotation、blur、compression、scan noise、crop、PDF raster DPI

劣化は意味構造を生成した後に適用し、clean vector、rasterized image、degraded imageの各座標変換をmanifestへ保存する。

## CI・再現性・ライセンス運用

1. Python、Schemdraw、Matplotlib、font、PDF rasterizer、ngspiceをversion pinする。Schemdrawがheadless backendを公式に案内していることは[公式手順](https://schemdraw.readthedocs.io/en/stable/usage/start.html)で確認できる。
2. sample IDは`profile + seed + schema_version`から決定し、全outputに同じIDを埋め込む。
3. 生成後にJSON Schema validation、bbox/page境界、端子―wire接触、junction次数、coil―contact参照整合、PDF/PNG存在、SHA-256を検査する。
4. ngspice適用対象ではbatch結果を期待状態と照合する。batch構文は[公式manual](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf)に従う。
5. 顧客図面の画像や記号を複製せず、公開仕様に基づく一般的な制御回路構造と自作記号だけで生成する。
6. 依存packageごとにLICENSE/SBOMを保存する。KiCad libraryを使う副出力では、design/generated fileへの例外とlibrary collection再配布の差を[公式library license](https://www.kicad.org/libraries/license/index.html)に従って扱う。
7. LTspiceをCI imageへ含めない。利用する場合はEULA本文を対象versionごとに保存・法務確認し、[公式EULA所在](https://support.analog.com/en-US/knowledgebase/article/ka-16982)を追跡する。

## 採用案

### Phase 1: 採用

- canonical circuit model
- Schemdraw renderer
- PDF/SVG/PNG exporter
- geometry/topology ground truth exporter
- deterministic seed、manifest、hash、schema validation
- 今回の失敗モードを狙う分岐黒丸・細線母線・relay/lamp混同の生成profile

### Phase 2: 条件付き採用

- ngspice adapter: 導通・接点状態を検証できる回路族のみ
- CircuiTikZ adapter: renderer/font/symbol domainを追加する場合
- KiCad adapter: EDA相互運用、ERC、公式CLI出力を検証する少数ケース

## 採用しない案

### LTspiceを正本・主レンダラーにする案

採用しない。公式配布にLinuxがなく、公式batch flagはsimulation/netlist中心でPDF exportを含まず、`.asc/.asy`の直接生成仕様も公開されず公式回答で非推奨である。[公式OS一覧](https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html)、[公式CLI一覧](https://www.analog.com/media/en/news-marketing-collateral/solutions-bulletins-brochures/ltspice-keyboard-shortcuts.pdf)、[公式回答](https://ez.analog.com/design-tools-and-calculators/ltspice/f/q-a/596907/syntax-definition-of-asy-and-asc-files)

### ngspice単独案

採用しない。netlistとbatch simulationには適するが、schematic PDF、symbol bbox、配線polyline、文字配置を生成しない。[公式manual](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf)、[公式処理フロー](https://ngspice.sourceforge.io/docs.html)

### KiCad形式を最初から唯一の正本にする案

採用しない。実現可能だが、`.kicad_sch`のUUID、library symbol、instance pathを含む大きな形式へ生成器が密結合し、大量ランダム生成の変更コストが高い。[公式schematic format](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/)

### CircuiTikZ sourceを唯一の正本にする案

採用しない。高品質PDFは得られるが、TeX描画命令だけでは電気的接続、coil―contact関係、評価用JSONを保証できず、別の意味モデルが結局必要になる。[公式manual](https://circuitikz.github.io/circuitikz/circuitikzmanualgit.pdf)

### 描画画像からground truthを再抽出する案

採用しない。合成データでは正解をrenderer入力から直接取得できるため、OCR/検出器で正解を再構成すると自己参照誤差が入り、誤差解析の基準にならない。Schemdrawはanchor/bboxを取得できるため、同一オブジェクトからGTを出せる。[Schemdraw Element API](https://schemdraw.readthedocs.io/en/stable/classes/drawing.html)

## 最小PoCの完了条件

1. 100 seed以上から、PDF/SVG/PNGとJSONを一括生成できる。
2. 同じcontainer imageとseedで、JSONとvector outputのhashが一致する。
3. relay coil、NO/NC contact、lamp、breaker、junction、thin bus、destination tagを含む。
4. 全symbolのbbox、全terminal、全wire polyline、全junction、全text、全connection graphがJSONに存在する。
5. PDFを現在の解析pipelineへ入力し、GTとのsymbol recall、wire recall、connection precision/recallを自動算出できる。
6. 失敗を、検出漏れ、分類違い、bboxずれ、端子割当違い、wire分割違い、junction role違い、文字照合違いへ分解できる。
7. synthetic-only改善と固定実図面評価を分離し、合成データだけで実運用精度を保証しない。
8. SBOM、各依存のlicense、生成器version、seed、font、renderer設定、SHA-256がmanifestに残る。

## 最終推奨

まずSchemdrawで「意味モデル → renderer → GT」の1本の決定的な経路を作る。ngspiceは意味検証に価値のある回路だけに適用し、画像生成を担わせない。十分な生成量と評価分解が成立した後に、CircuiTikZを第2rendererとして追加して見た目の偏りを減らす。KiCadは少数の相互運用ケースに限定する。LTspiceは公式Linux/headless PDF要件を満たさないため基盤から外す。

この方式が、今回のリポジトリで「大量生成」「コードから完全再現」「PDF/画像とground truthの同時生成」「CI実行」「ライセンス条件の追跡」を最小の実装・運用リスクで両立する。
