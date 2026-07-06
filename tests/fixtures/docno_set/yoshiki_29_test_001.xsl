<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">
<xsl:output method="html" encoding="shift_jis" indent="yes"/>

<xsl:template match="/TestTsuchiShoList">
<HTML>
<HEAD><TITLE>テスト通知書</TITLE></HEAD>
<BODY>
  <xsl:apply-templates select="testTsuchiSho"/>
</BODY>
</HTML>
</xsl:template>

<xsl:template match="testTsuchiSho">
  <p align="center"><b>テスト通知書（合成テストデータ）</b></p>
  <p align="right"><xsl:value-of select="hakkouYmd"/></p>
  <table border="1" cellspacing="0" cellpadding="4">
    <tr><td>事業所整理記号</td><td><xsl:value-of select="jigyoshoSeiriKigo"/></td></tr>
    <tr><td>事業所番号</td><td><xsl:value-of select="jigyoshoNum"/></td></tr>
    <tr><td>事業所名称</td><td><xsl:value-of select="jigyoshoName"/></td></tr>
    <tr><td>合計額</td><td><xsl:value-of select="goukeigaku"/>円</td></tr>
  </table>
  <p><xsl:value-of select="oshirase"/></p>
</xsl:template>
</xsl:stylesheet>
