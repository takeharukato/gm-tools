# リリース手順

GitHub Actionを用いて, 以下の手順でリリース作業を実施します。

* まず main を最新化しておきたいブランチをマージし, リリース内容を確定させます。
* autogen.sh やバージョン番号 (configure.ac, rpm/debian の spec/changelog 等) を更新してコミットし, main に反映します。
* git tag vX.Y.Z のように新しいタグを main の HEAD に付けて git push origin --tags します。
* タグ push がトリガーとなり GitHub Actions が make dist, make rpm, make deb を実行し, 生成された tarball/RPM/DEB をリリースに添付します。
* 最後に GitHub の Release を確認し, 必要であれば自動生成された Release Notes を調整して公開します。
